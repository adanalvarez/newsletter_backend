"""Amazon SES: the confirmation email and the contact list (which also handles unsubscribe)."""

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
        message = messages.confirmation(link, self.settings.site_url)
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
            "ConfigurationSetName": self.settings.confirmation_configuration_set,
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
