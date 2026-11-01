"""SES events from EventBridge: bounces, complaints and unsubscribes.

SES already stops sending to addresses on its suppression list, and the contact list records who unsubscribed.
This keeps the subscriber records and their consent history in step with that, and starts the retention clock.
Events that fail here go to a dead-letter queue (infra/events.tf), so none is lost silently.
"""

import logging
import time

from .validation import canonical, email_ref, normalize_email

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class Deps:
    def __init__(self, settings, store, mailer, now=lambda: int(time.time())):
        self.settings = settings
        self.store = store
        self.mailer = mailer
        self.now = now


_deps = None


def lambda_handler(event, context):
    global _deps
    if _deps is None:
        import boto3

        from . import config
        from .mailer import Mailer
        from .store import Store

        settings = config.load()
        table = boto3.resource("dynamodb").Table(settings.table_name)
        _deps = Deps(settings, Store(table, settings), Mailer(boto3.client("sesv2"), settings))
    handle(event, _deps)


def handle(event, deps):
    detail = event.get("detail") or {}
    kind = detail.get("eventType")
    now = deps.now()
    # Editions carry an "edition" tag (tools/cli.py); confirmation emails don't.
    edition = (detail.get("mail", {}).get("tags", {}).get("edition") or [None])[0]

    if kind == "Send" and edition:
        deps.store.count_edition(edition, "sent")
    elif kind == "Delivery" and edition:
        deps.store.count_edition(edition, "delivered")

    if kind == "Bounce":
        bounce = detail.get("bounce", {})
        # Temporary bounces (full mailbox, server down) are retried by SES; only permanent ones remove a reader.
        if bounce.get("bounceType") == "Permanent":
            recipients = bounce.get("bouncedRecipients", [])
            for recipient in recipients:
                drop(recipient.get("emailAddress"), "bounced", deps, now)
            if edition:
                deps.store.count_edition(edition, "bounced", len(recipients))

    elif kind == "Complaint":
        recipients = detail.get("complaint", {}).get("complainedRecipients", [])
        for recipient in recipients:
            drop(recipient.get("emailAddress"), "complained", deps, now)
        if edition:
            deps.store.count_edition(edition, "complained", len(recipients))

    elif kind == "Subscription":
        preferences = detail.get("subscription", {}).get("newTopicPreferences", {})
        opted_out = preferences.get("unsubscribeAll") or any(
            topic.get("topicName") == deps.settings.topic and topic.get("subscriptionStatus", "").lower().replace("_", "") == "optout"
            for topic in preferences.get("topicSubscriptionStatus", [])
        )
        if opted_out:
            if edition:
                deps.store.count_edition(edition, "unsubscribed")
            for address in detail.get("mail", {}).get("destination", []):
                email = normalize_email(address)
                if email:
                    key = canonical(email)
                    deps.store.set_status(key, email, "unsubscribed", now, "unsubscribe link")
                    logger.info("unsubscribed: %s", email_ref(key, deps.settings.log_hash_key))


def drop(address, status, deps, now):
    email = normalize_email(address)
    if not email:
        return
    key = canonical(email)
    deps.store.set_status(key, email, status, now, status)
    deps.mailer.remove_contact(email)
    logger.info("%s: %s removed", status, email_ref(key, deps.settings.log_hash_key))
