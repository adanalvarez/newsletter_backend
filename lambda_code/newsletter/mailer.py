"""Amazon SES: the confirmation and welcome emails, and the contact list (which also handles unsubscribe)."""

import json
from email.utils import formataddr, parseaddr

from botocore.exceptions import ClientError

from . import messages
from .errors import Rejected


def encode_sender(address):
    # A display name with non-ASCII characters ("Álvarez") must be RFC 2047-encoded, or SES rejects the send.
    name, email = parseaddr(address)
    return formataddr((name, email), charset="utf-8")


class Mailer:
    def __init__(self, ses, settings):
        self.ses = ses
        self.settings = settings
        self.sender = encode_sender(settings.from_address)

    def send_confirmation(self, email, link):
        self._send(email, messages.confirmation(link, self.settings.site_url), ConfigurationSetName=self.settings.confirmation_configuration_set)

    def send_welcome(self, email):
        """Once someone confirms. It goes through the contact list like an edition: SES adds the one-click
        unsubscribe headers and fills in the unsubscribe link, and the tag counts it under "welcome"."""
        self._send(
            email,
            messages.welcome(self.settings.site_url),
            ConfigurationSetName=self.settings.configuration_set,
            ListManagementOptions={"ContactListName": self.settings.contact_list, "TopicName": self.settings.topic},
            EmailTags=[{"Name": "edition", "Value": messages.WELCOME_TAG}],
        )

    def _send(self, email, message, **options):
        request = {
            "FromEmailAddress": self.sender,
            "Destination": {"ToAddresses": [email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message["subject"], "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": message["text"], "Charset": "UTF-8"},
                        "Html": {"Data": message["html"], "Charset": "UTF-8"},
                    },
                }
            },
            **options,
        }
        if self.settings.reply_to:
            request["ReplyToAddresses"] = [self.settings.reply_to]
        try:
            self.ses.send_email(**request)
        except ClientError as error:
            if error.response["Error"]["Code"] in ("BadRequestException", "MessageRejected"):
                raise Rejected(error.response["Error"]["Code"]) from None
            raise

    def subscribe_contact(self, email, attributes):
        contact = {
            "ContactListName": self.settings.contact_list,
            "EmailAddress": email,
            "TopicPreferences": [{"TopicName": self.settings.topic, "SubscriptionStatus": "OPT_IN"}],
            "UnsubscribeAll": False,
            "AttributesData": json.dumps(attributes),
        }
        try:
            self.ses.create_contact(**contact)
        except self.ses.exceptions.AlreadyExistsException:
            # Someone who unsubscribed and signed up (and confirmed) again.
            self.ses.update_contact(**contact)

    def remove_contact(self, email):
        try:
            self.ses.delete_contact(ContactListName=self.settings.contact_list, EmailAddress=email)
        except self.ses.exceptions.NotFoundException:
            pass
