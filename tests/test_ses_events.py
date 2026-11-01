import unittest

from fakes import NOW, SETTINGS, FakeMailer, FakeStore

from newsletter import ses_events


def event(detail):
    return {"source": "aws.ses", "detail": detail}


class SesEventsTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.mailer = FakeMailer()
        self.deps = ses_events.Deps(SETTINGS, self.store, self.mailer, now=lambda: NOW)

    def test_permanent_bounce_removes_the_reader(self):
        ses_events.handle(
            event({"eventType": "Bounce", "bounce": {"bounceType": "Permanent", "bouncedRecipients": [{"emailAddress": "Gone@Example.com"}]}}),
            self.deps,
        )
        self.assertEqual(self.store.subscribers["gone@example.com"]["status"], "bounced")
        self.assertEqual(self.mailer.removed, ["gone@example.com"])

    def test_temporary_bounce_is_left_to_ses(self):
        ses_events.handle(
            event({"eventType": "Bounce", "bounce": {"bounceType": "Transient", "bouncedRecipients": [{"emailAddress": "full@example.com"}]}}),
            self.deps,
        )
        self.assertEqual(self.store.subscribers, {})
        self.assertEqual(self.mailer.removed, [])

    def test_complaint_removes_the_reader(self):
        ses_events.handle(
            event({"eventType": "Complaint", "complaint": {"complainedRecipients": [{"emailAddress": "angry@example.com"}]}}),
            self.deps,
        )
        self.assertEqual(self.store.subscribers["angry@example.com"]["status"], "complained")
        self.assertEqual(self.mailer.removed, ["angry@example.com"])

    def test_unsubscribe_is_recorded_in_the_consent_log(self):
        ses_events.handle(
            event(
                {
                    "eventType": "Subscription",
                    "mail": {"destination": ["reader@example.com"]},
                    "subscription": {
                        "contactList": "readers",
                        "newTopicPreferences": {
                            "unsubscribeAll": False,
                            "topicSubscriptionStatus": [{"topicName": "newsletter", "subscriptionStatus": "OptOut"}],
                        },
                    },
                }
            ),
            self.deps,
        )
        self.assertEqual(self.store.subscribers["reader@example.com"]["status"], "unsubscribed")
        self.assertEqual(self.store.subscribers["reader@example.com"]["history"], [("unsubscribed", "unsubscribe link")])

    def test_opting_in_again_is_not_an_unsubscribe(self):
        ses_events.handle(
            event(
                {
                    "eventType": "Subscription",
                    "mail": {"destination": ["reader@example.com"]},
                    "subscription": {
                        "newTopicPreferences": {
                            "unsubscribeAll": False,
                            "topicSubscriptionStatus": [{"topicName": "newsletter", "subscriptionStatus": "OptIn"}],
                        }
                    },
                }
            ),
            self.deps,
        )
        self.assertEqual(self.store.subscribers, {})

    def test_bounce_of_a_variant_address_updates_the_canonical_record(self):
        ses_events.handle(
            event({"eventType": "Bounce", "bounce": {"bounceType": "Permanent", "bouncedRecipients": [{"emailAddress": "g.one+x@gmail.com"}]}}),
            self.deps,
        )
        self.assertEqual(self.store.subscribers["gone@gmail.com"]["status"], "bounced")
        self.assertEqual(self.mailer.removed, ["g.one+x@gmail.com"])

    def test_edition_numbers_are_counted_from_tagged_events(self):
        tags = {"mail": {"tags": {"edition": ["sixty-seconds"]}, "destination": ["r@example.com"]}}
        ses_events.handle(event({"eventType": "Send", **tags}), self.deps)
        ses_events.handle(event({"eventType": "Delivery", **tags}), self.deps)
        ses_events.handle(event({"eventType": "Bounce", **tags, "bounce": {"bounceType": "Permanent", "bouncedRecipients": [{"emailAddress": "r@example.com"}]}}), self.deps)
        ses_events.handle(
            event({"eventType": "Subscription", **tags, "subscription": {"newTopicPreferences": {"unsubscribeAll": True}}}),
            self.deps,
        )
        self.assertEqual(self.store.editions["sixty-seconds"], {"sent": 1, "delivered": 1, "bounced": 1, "unsubscribed": 1})

    def test_untagged_mail_is_not_counted_as_an_edition(self):
        ses_events.handle(event({"eventType": "Send", "mail": {"tags": {}}}), self.deps)
        self.assertEqual(self.store.editions, {})

    def test_other_events_are_ignored(self):
        ses_events.handle(event({"eventType": "Delivery"}), self.deps)
        self.assertEqual((self.store.subscribers, self.mailer.removed), ({}, []))


if __name__ == "__main__":
    unittest.main()
