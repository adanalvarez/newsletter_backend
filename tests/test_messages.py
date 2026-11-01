import unittest

from fakes import SETTINGS

from newsletter import messages
from newsletter.mailer import Mailer


class FakeSES:
    def __init__(self):
        self.requests = []

    def send_email(self, **request):
        self.requests.append(request)
        return {"MessageId": "m-1"}


class MessagesTest(unittest.TestCase):
    def test_confirmation_carries_the_link_and_nothing_else_from_the_visitor(self):
        message = messages.confirmation("https://adan.cloud/newsletter/confirm#t=abc", "https://adan.cloud")
        self.assertIn('href="https://adan.cloud/newsletter/confirm#t=abc"', message["html"])
        self.assertIn("https://adan.cloud/newsletter/confirm#t=abc", message["text"])
        self.assertNotIn("$", message["html"])

    def test_welcome_has_the_unsubscribe_placeholder_and_escaped_text(self):
        message = messages.welcome("https://adan.cloud")
        self.assertEqual(message["subject"], "Welcome to No Hype, No Fear")
        self.assertIn('href="{{amazonSESUnsubscribeUrl}}"', message["html"])
        self.assertIn("Unsubscribe: {{amazonSESUnsubscribeUrl}}", message["text"])
        self.assertIn("I&#x27;m Adan Álvarez", message["html"])
        self.assertIn("https://adan.cloud/writing", message["text"])
        self.assertNotIn("$", message["html"])

    def test_welcome_goes_through_the_contact_list_and_is_tagged(self):
        ses = FakeSES()
        Mailer(ses, SETTINGS).send_welcome("reader@example.com")
        request = ses.requests[0]
        self.assertEqual(request["Destination"], {"ToAddresses": ["reader@example.com"]})
        self.assertEqual(request["ConfigurationSetName"], "newsletter")
        self.assertEqual(request["ListManagementOptions"], {"ContactListName": "readers", "TopicName": "newsletter"})
        self.assertEqual(request["EmailTags"], [{"Name": "edition", "Value": "welcome"}])
        self.assertEqual(request["FromEmailAddress"], "=?utf-8?q?Adan_=C3=81lvarez?= <adan@example.com>")

    def test_confirmation_goes_through_its_own_configuration_set_without_list_management(self):
        ses = FakeSES()
        Mailer(ses, SETTINGS).send_confirmation("reader@example.com", "https://adan.cloud/newsletter/confirm#t=abc")
        request = ses.requests[0]
        self.assertEqual(request["ConfigurationSetName"], "newsletter-confirmations")
        self.assertNotIn("ListManagementOptions", request)


if __name__ == "__main__":
    unittest.main()
