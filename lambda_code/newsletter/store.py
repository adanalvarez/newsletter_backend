"""DynamoDB access. One table, three kinds of items:

- SUB#<canonical email>  one subscriber: the address as typed, status, role, source page, consent version, dates,
                         and `history`, the consent log (every confirmation and withdrawal). Keeping the log in
                         the same item means retention and erasure cover it in one place.
- TOKEN#<sha256>         a pending confirmation link (expires; only the hash of the token is kept)
- COUNTER#<date>         confirmation emails sent that day (for the daily ceiling)

Writes that depend on the current state are conditional, so two requests at once can't both send an email,
confirm twice, or overwrite a confirmed subscriber.
"""

import secrets

from botocore.exceptions import ClientError

from .clock import iso

DAY = 86400


def _conditional_failed(error):
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


class Store:
    def __init__(self, table, settings):
        self.table = table
        self.settings = settings

    def get_subscriber(self, key):
        return self.table.get_item(Key={"pk": f"SUB#{key}"}).get("Item")

    def save_pending(self, key, email, role, source, consent_version, now, token_hash, token_expires, resend_cutoff):
        """Records a sign-up waiting for confirmation. Returns False, and changes nothing, if the subscriber is
        already confirmed or blocked, or had a confirmation email after `resend_cutoff`."""
        try:
            self.table.update_item(
                Key={"pk": f"SUB#{key}"},
                UpdateExpression=(
                    "SET #e = :e, #s = :pending, #r = :r, #src = :src, #v = :v, "
                    "requested_at = :t, last_sent_at = :now, expires_at = :exp"
                ),
                ConditionExpression="attribute_not_exists(pk) OR #s = :unsub OR (#s = :pending AND last_sent_at < :cutoff)",
                ExpressionAttributeNames={"#e": "email", "#s": "status", "#r": "role", "#src": "source", "#v": "consent_version"},
                ExpressionAttributeValues={
                    ":e": email,
                    ":pending": "pending",
                    ":unsub": "unsubscribed",
                    ":r": role,
                    ":src": source,
                    ":v": consent_version,
                    ":t": iso(now),
                    ":now": now,
                    # Unconfirmed sign-ups disappear on their own once the link has expired.
                    ":exp": token_expires,
                    ":cutoff": resend_cutoff,
                },
            )
        except ClientError as error:
            if _conditional_failed(error):
                return False
            raise
        self.table.put_item(Item={"pk": f"TOKEN#{token_hash}", "key": key, "email": email, "expires_at": token_expires})
        return True

    def take_token(self, token_hash, now):
        """Uses up a confirmation link: deleted and returned in one step, so it can only work once."""
        try:
            item = self.table.delete_item(
                Key={"pk": f"TOKEN#{token_hash}"},
                ConditionExpression="attribute_exists(pk)",
                ReturnValues="ALL_OLD",
            ).get("Attributes")
        except ClientError as error:
            if _conditional_failed(error):
                return None
            raise
        # DynamoDB deletes expired items late, so check the expiry here too.
        if not item or int(item.get("expires_at", 0)) < now:
            return None
        return item

    def restore_token(self, token_hash, item):
        # Puts a link back when confirming failed half-way, so the reader can try again.
        self.table.put_item(Item={**item, "pk": f"TOKEN#{token_hash}"})

    def confirm(self, key, now, consent_version, source):
        """Marks a pending subscriber confirmed. Returns False if they weren't pending (already confirmed)."""
        try:
            self.table.update_item(
                Key={"pk": f"SUB#{key}"},
                UpdateExpression=(
                    "SET #s = :confirmed, confirmed_at = :t, #v = :v, "
                    "#h = list_append(if_not_exists(#h, :empty), :event) REMOVE expires_at"
                ),
                ConditionExpression="#s = :pending",
                ExpressionAttributeNames={"#s": "status", "#v": "consent_version", "#h": "history"},
                ExpressionAttributeValues={
                    ":confirmed": "confirmed",
                    ":pending": "pending",
                    ":t": iso(now),
                    ":v": consent_version,
                    ":empty": [],
                    ":event": [{"event": "confirmed", "at": iso(now), "consent_version": consent_version, "source": source}],
                },
            )
            return True
        except ClientError as error:
            if _conditional_failed(error):
                return False
            raise

    def set_status(self, key, email, status, now, reason):
        """Records an unsubscribe, bounce or complaint, and starts the retention clock. Addresses that never
        confirmed (someone else typed them in) are kept only briefly."""
        current = self.get_subscriber(key) or {}
        confirmed_before = current.get("status") not in (None, "pending") or "confirmed_at" in current
        days = self.settings.retention_days if confirmed_before else self.settings.unconfirmed_retention_days
        self.table.update_item(
            Key={"pk": f"SUB#{key}"},
            UpdateExpression="SET #e = if_not_exists(#e, :e), #s = :s, updated_at = :t, expires_at = :exp, "
            "#h = list_append(if_not_exists(#h, :empty), :event)",
            ExpressionAttributeNames={"#e": "email", "#s": "status", "#h": "history"},
            ExpressionAttributeValues={
                ":e": email,
                ":s": status,
                ":t": iso(now),
                ":exp": now + days * DAY,
                ":empty": [],
                ":event": [{"event": status, "at": iso(now), "reason": reason}],
            },
        )

    def count_edition(self, edition, field, amount=1):
        """Per-edition numbers (sent, delivered, bounced, complained, unsubscribed). Counts only, no addresses."""
        self.table.update_item(
            Key={"pk": f"EDITION#{edition}"},
            UpdateExpression="ADD #f :n",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":n": amount},
        )

    def record_vote(self, edition, question, answer, voter, now):
        """Counts an anonymous answer, once per reader per question per day. Returns False for a repeat."""
        try:
            self.table.put_item(
                Item={"pk": f"VOTER#{edition}#{question}#{voter}", "expires_at": now + 26 * 3600},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            if _conditional_failed(error):
                return False
            raise
        self.table.update_item(
            Key={"pk": f"POLL#{edition}#{question}"},
            UpdateExpression="ADD #a :one, #t :one",
            ExpressionAttributeNames={"#a": f"a{answer}", "#t": "total"},
            ExpressionAttributeValues={":one": 1},
        )
        return True

    def add_comment(self, edition, question, answer, comment, now):
        self.table.put_item(
            Item={
                "pk": f"COMMENT#{edition}#{question}#{iso(now)}#{secrets.token_hex(4)}",
                "answer": answer,
                "comment": comment,
                "at": iso(now),
                "expires_at": now + self.settings.comment_retention_days * DAY,
            }
        )

    def count_confirmation(self, day, limit, now):
        """Adds one to today's counter. Returns False once the daily ceiling is reached."""
        try:
            self.table.update_item(
                Key={"pk": f"COUNTER#{day}"},
                UpdateExpression="ADD #c :one SET expires_at = if_not_exists(expires_at, :exp)",
                ConditionExpression="attribute_not_exists(#c) OR #c < :limit",
                ExpressionAttributeNames={"#c": "count"},
                ExpressionAttributeValues={":one": 1, ":limit": limit, ":exp": now + 7 * DAY},
            )
            return True
        except ClientError as error:
            if _conditional_failed(error):
                return False
            raise
