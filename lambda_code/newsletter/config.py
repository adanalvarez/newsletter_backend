"""Settings, read once from the Lambda environment (set by Terraform)."""

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    table_name: str = ""
    contact_list: str = ""
    topic: str = "newsletter"
    # Confirmation emails have their own configuration set, so they can be paused without stopping editions.
    confirmation_configuration_set: str = ""
    from_address: str = ""
    reply_to: str = ""
    site_url: str = "https://adan.cloud"
    # CloudFront adds X-Origin-Verify and Cloudflare adds X-Edge-Verify to every request they forward. Each is a
    # list so a secret can be rotated: add the new one, update the header, then remove the old one.
    origin_secrets: list = field(default_factory=list)
    edge_secrets: list = field(default_factory=list)
    log_hash_key: str = ""
    # Privacy-notice versions the site may send with a sign-up. The first one is the current version.
    consent_versions: list = field(default_factory=list)
    roles: list = field(default_factory=list)
    # A ceiling on confirmation emails per day, so a flood of fake sign-ups can't hurt the sending reputation.
    daily_limit: int = 100
    token_ttl_hours: int = 48
    resend_after_hours: int = 24
    # How long records are kept after someone unsubscribes, bounces or complains (proof of consent and of
    # withdrawal). Addresses that never confirmed are kept much shorter.
    retention_days: int = 1095
    unconfirmed_retention_days: int = 30
    # Every sign-up answer takes about this long, so response times don't show who's already subscribed.
    min_response_ms: int = 700
    # Polls and "was this useful?": the key for the daily, one-way voter code that stops repeat votes, where
    # the site publishes the list of open questions, and how long anonymous comments are kept.
    vote_key: str = ""
    polls_bucket: str = ""
    polls_key: str = "newsletter/polls.json"
    comment_retention_days: int = 365


def _list(value):
    return [item for item in (value or "").split(",") if item]


def load():
    env = os.environ
    return Settings(
        table_name=env.get("TABLE_NAME", ""),
        contact_list=env.get("CONTACT_LIST", ""),
        topic=env.get("TOPIC", "newsletter"),
        confirmation_configuration_set=env.get("CONFIRMATION_CONFIGURATION_SET", ""),
        from_address=env.get("FROM_ADDRESS", ""),
        reply_to=env.get("REPLY_TO", ""),
        site_url=env.get("SITE_URL", "https://adan.cloud").rstrip("/"),
        origin_secrets=_list(env.get("ORIGIN_SECRETS")),
        edge_secrets=_list(env.get("EDGE_SECRETS")),
        log_hash_key=env.get("LOG_HASH_KEY", ""),
        consent_versions=json.loads(env.get("CONSENT_VERSIONS", "[]")),
        roles=json.loads(env.get("ROLES", "[]")),
        daily_limit=int(env.get("DAILY_CONFIRMATION_LIMIT", "100")),
        token_ttl_hours=int(env.get("TOKEN_TTL_HOURS", "48")),
        resend_after_hours=int(env.get("RESEND_AFTER_HOURS", "24")),
        retention_days=int(env.get("RETENTION_DAYS", "1095")),
        unconfirmed_retention_days=int(env.get("UNCONFIRMED_RETENTION_DAYS", "30")),
        min_response_ms=int(env.get("MIN_RESPONSE_MS", "700")),
        vote_key=env.get("VOTE_KEY", ""),
        polls_bucket=env.get("POLLS_BUCKET", ""),
        polls_key=env.get("POLLS_KEY", "newsletter/polls.json"),
        comment_retention_days=int(env.get("COMMENT_RETENTION_DAYS", "365")),
    )
