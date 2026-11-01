"""In-memory stand-ins for the DynamoDB store and SES, so the handlers can be tested without AWS."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda_code"))

from newsletter.config import Settings  # noqa: E402
from newsletter.errors import Rejected  # noqa: E402

NOW = 1_800_000_000  # a fixed clock

SETTINGS = Settings(
    table_name="t",
    contact_list="readers",
    topic="newsletter",
    confirmation_configuration_set="newsletter-confirmations",
    from_address="Adan Álvarez <adan@example.com>",
    site_url="https://adan.cloud",
    origin_secrets=["s3cret", "n3w-s3cret"],
    edge_secrets=["3dge"],
    log_hash_key="k",
    consent_versions=["2026-11-01", "2026-06-01"],
    roles=["Detection engineering", "Other"],
    daily_limit=2,
    vote_key="v0te",
)


class FakeStore:
    """Mirrors store.Store, including its conditional writes."""

    def __init__(self):
        self.subscribers = {}
        self.tokens = {}
        self.counters = {}
        self.statuses = []
        self.editions = {}
        self.voters = set()
        self.polls = {}
        self.comments = []

    def get_subscriber(self, key):
        return self.subscribers.get(key)

    def save_pending(self, key, email, role, source, consent_version, now, token_hash, token_expires, resend_cutoff):
        current = self.subscribers.get(key)
        allowed = (
            current is None
            or current["status"] == "unsubscribed"
            or (current["status"] == "pending" and current["last_sent_at"] < resend_cutoff)
        )
        if not allowed:
            return False
        self.subscribers[key] = {
            **(current or {}),
            "email": email,
            "status": "pending",
            "role": role,
            "source": source,
            "consent_version": consent_version,
            "last_sent_at": now,
        }
        self.tokens[token_hash] = {"key": key, "email": email, "expires_at": token_expires}
        return True

    def take_token(self, token_hash, now):
        item = self.tokens.pop(token_hash, None)
        return item if item and item["expires_at"] >= now else None

    def restore_token(self, token_hash, item):
        self.tokens[token_hash] = item

    def confirm(self, key, now, consent_version, source):
        subscriber = self.subscribers.get(key)
        if not subscriber or subscriber["status"] != "pending":
            return False
        subscriber["status"] = "confirmed"
        subscriber.setdefault("history", []).append(("confirmed", consent_version, source))
        return True

    def set_status(self, key, email, status, now, reason):
        subscriber = self.subscribers.setdefault(key, {"email": email})
        subscriber["status"] = status
        subscriber.setdefault("history", []).append((status, reason))
        self.statuses.append((key, status))

    def count_edition(self, edition, field, amount=1):
        numbers = self.editions.setdefault(edition, {})
        numbers[field] = numbers.get(field, 0) + amount

    def record_vote(self, edition, question, answer, voter, now):
        key = (edition, question, voter)
        if key in self.voters:
            return False
        self.voters.add(key)
        counts = self.polls.setdefault((edition, question), {})
        counts[answer] = counts.get(answer, 0) + 1
        return True

    def add_comment(self, edition, question, answer, comment, now):
        self.comments.append((edition, question, answer, comment))

    def count_confirmation(self, day, limit, now):
        if self.counters.get(day, 0) >= limit:
            return False
        self.counters[day] = self.counters.get(day, 0) + 1
        return True


class FakeMailer:
    def __init__(self, reject=False, fail_contact=False):
        self.sent = []
        self.contacts = {}
        self.removed = []
        self.reject = reject
        self.fail_contact = fail_contact

    def send_confirmation(self, email, link):
        if self.reject:
            raise Rejected("BadRequestException")
        self.sent.append((email, link))

    def subscribe_contact(self, email, attributes):
        if self.fail_contact:
            raise RuntimeError("SES is down")
        self.contacts[email] = attributes

    def remove_contact(self, email):
        self.removed.append(email)
