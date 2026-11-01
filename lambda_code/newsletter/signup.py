"""Sign-up endpoints, reached through Cloudflare, CloudFront (adan.cloud/api/*) and API Gateway.

POST /api/subscribe  the form on adan.cloud: stores a pending sign-up and emails a confirmation link
POST /api/confirm    the button on adan.cloud/newsletter/confirm: confirms the sign-up
GET  /api/confirm    sends old or scanned links to that page, so a link scanner can never confirm anyone

Every answer is a redirect back to adan.cloud, the same whether the address is new, pending or already
subscribed, and it takes about the same time. So the form can't be used to find out who reads the newsletter.
"""

import base64
import logging
import random
import secrets
import time
from urllib.parse import parse_qs

from . import config
from .clock import day, iso
from .errors import Rejected
from .validation import (
    canonical,
    clean_role,
    clean_source,
    email_ref,
    normalize_email,
    secret_matches,
    token_hash,
    valid_token,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CHECK_INBOX = "/newsletter/check-your-inbox"
CONFIRMED = "/newsletter/confirmed"
EXPIRED = "/newsletter/link-expired"
CONFIRM_PAGE = "/newsletter/confirm"
MAX_BODY = 4096
# The log line the daily-ceiling alarm watches for (infra/alarms.tf).
CEILING_MARKER = "DAILY_CEILING_REACHED"


class Deps:
    def __init__(self, settings, store, mailer, now=lambda: int(time.time()), clock=time.monotonic, sleep=time.sleep):
        self.settings = settings
        self.store = store
        self.mailer = mailer
        self.now = now
        self.clock = clock
        self.sleep = sleep


_deps = None


def lambda_handler(event, context):
    global _deps
    if _deps is None:
        import boto3

        from .mailer import Mailer
        from .store import Store

        settings = config.load()
        table = boto3.resource("dynamodb").Table(settings.table_name)
        _deps = Deps(settings, Store(table, settings), Mailer(boto3.client("sesv2"), settings))
    return handle(event, _deps)


def handle(event, deps):
    settings = deps.settings
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    # Only requests that went through Cloudflare (its rate limits) and the site's CloudFront are answered.
    if not secret_matches(headers.get("x-origin-verify"), settings.origin_secrets):
        return text(403, "Forbidden")
    if settings.edge_secrets and not secret_matches(headers.get("x-edge-verify"), settings.edge_secrets):
        return text(403, "Forbidden")

    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    # Forms on other sites can't post here for their visitors (which would spread abuse across many IPs).
    if method == "POST" and not same_site(headers, settings.site_url):
        return text(403, "Forbidden")

    if path == "/api/subscribe" and method == "POST":
        started = deps.clock()
        try:
            return subscribe(form(event), deps)
        finally:
            pad(started, deps)
    if path == "/api/confirm" and method == "POST":
        return confirm(form(event), deps)
    if path == "/api/confirm" and method == "GET":
        token = (event.get("queryStringParameters") or {}).get("t", "")
        return redirect(settings, CONFIRM_PAGE + (f"#t={token}" if valid_token(token) else ""))
    return text(404, "Not found")


def same_site(headers, site_url):
    origin = headers.get("origin")
    if origin:
        return origin == site_url
    return headers.get("sec-fetch-site") == "same-origin"


def pad(started, deps):
    """Makes every sign-up answer take about the same time, whether or not an email was sent."""
    target = deps.settings.min_response_ms / 1000 + random.uniform(0, 0.15)
    remaining = target - (deps.clock() - started)
    if remaining > 0:
        deps.sleep(remaining)


def subscribe(fields, deps):
    settings, store = deps.settings, deps.store

    # The honeypot field is invisible to people. Bots that fill it get the normal answer and nothing else.
    if fields.get("website"):
        logger.info("subscribe: honeypot filled, ignored")
        return redirect(settings, CHECK_INBOX)

    email = normalize_email(fields.get("email"))
    if not email:
        return text(400, "That email address doesn't look right. Please go back and check it.")

    key = canonical(email)
    ref = email_ref(key, settings.log_hash_key)
    now = deps.now()
    subscriber = store.get_subscriber(key)
    status = subscriber.get("status") if subscriber else None
    resend_cutoff = now - settings.resend_after_hours * 3600

    if status in ("confirmed", "bounced", "complained"):
        logger.info("subscribe: %s already %s, nothing sent", ref, status)
        return redirect(settings, CHECK_INBOX)
    if status == "pending" and int(subscriber.get("last_sent_at", 0)) >= resend_cutoff:
        logger.info("subscribe: %s confirmation sent recently, nothing sent", ref)
        return redirect(settings, CHECK_INBOX)
    if not store.count_confirmation(day(now), settings.daily_limit, now):
        logger.warning("%s: %s confirmations today, nothing sent to %s", CEILING_MARKER, settings.daily_limit, ref)
        return redirect(settings, CHECK_INBOX)

    token = secrets.token_urlsafe(32)
    saved = store.save_pending(
        key,
        email,
        clean_role(fields.get("role", ""), settings.roles),
        clean_source(fields.get("source", "")),
        consent_version(fields.get("consent_version", ""), settings, ref),
        now,
        token_hash(token),
        now + settings.token_ttl_hours * 3600,
        resend_cutoff,
    )
    if not saved:
        # Another request for the same inbox got there first.
        logger.info("subscribe: %s changed meanwhile, nothing sent", ref)
        return redirect(settings, CHECK_INBOX)

    try:
        # The code goes after "#", so it never reaches server or CDN logs, or a Referer header.
        deps.mailer.send_confirmation(email, f"{settings.site_url}{CONFIRM_PAGE}#t={token}")
    except Rejected as error:
        logger.warning("subscribe: SES refused %s (%s), nothing sent", ref, error)
        return redirect(settings, CHECK_INBOX)
    logger.info("subscribe: confirmation sent to %s", ref)
    return redirect(settings, CHECK_INBOX)


def confirm(fields, deps):
    settings, store = deps.settings, deps.store
    token = fields.get("t", "")
    if not valid_token(token):
        return redirect(settings, EXPIRED)

    now = deps.now()
    hashed = token_hash(token)
    pending = store.take_token(hashed, now)
    if not pending:
        return redirect(settings, EXPIRED)

    key, email = pending["key"], pending["email"]
    subscriber = store.get_subscriber(key) or {}
    source = subscriber.get("source", "")
    try:
        deps.mailer.subscribe_contact(email, {"role": subscriber.get("role", ""), "source": source, "confirmed_at": iso(now)})
        store.confirm(key, now, subscriber.get("consent_version") or current_consent(settings), source)
    except Exception:
        # Put the link back so the reader can try again, then let the error raise the alarm.
        store.restore_token(hashed, pending)
        raise
    logger.info("confirm: %s confirmed", email_ref(key, settings.log_hash_key))
    return redirect(settings, CONFIRMED)


def consent_version(posted, settings, ref):
    """The privacy-notice version the page showed. Only known versions are recorded."""
    if posted in settings.consent_versions:
        return posted
    if posted:
        logger.warning("subscribe: unknown consent version from the page for %s; recording the current one", ref)
    return current_consent(settings)


def current_consent(settings):
    return settings.consent_versions[0] if settings.consent_versions else ""


def form(event):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        except ValueError:
            return {}
    if len(body) > MAX_BODY:
        return {}
    try:
        return {key: values[0] for key, values in parse_qs(body, max_num_fields=20).items()}
    except ValueError:
        return {}


def redirect(settings, path):
    return {"statusCode": 303, "headers": {"Location": settings.site_url + path, "Cache-Control": "no-store"}}


def text(status, message):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
        "body": message,
    }
