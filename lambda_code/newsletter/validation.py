"""Input checks. Everything from the form is untrusted and is either validated or dropped."""

import hashlib
import hmac
import re

# ASCII dot-atom local part (SES can't send to non-ASCII local parts) and a domain of valid labels.
LOCAL_RE = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*")
DOMAIN_RE = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{30,100}")
SOURCE_RE = re.compile(r"/[A-Za-z0-9/_-]{0,99}")
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def normalize_email(value):
    email = (value or "").strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        return None
    local, domain = email.split("@")
    if len(local) > 64 or not LOCAL_RE.fullmatch(local) or not DOMAIN_RE.fullmatch(domain):
        return None
    return email


def canonical(email):
    """The key that recognises one inbox behind its variants: drops +tags, and dots for Gmail.
    Used to limit confirmation emails per inbox; the email itself still goes to the address as typed."""
    local, domain = email.split("@")
    local = local.split("+", 1)[0] or local
    if domain in GMAIL_DOMAINS:
        local, domain = local.replace(".", "") or local, "gmail.com"
    return f"{local}@{domain}"


def clean_role(value, allowed):
    return value if value in allowed else ""


def clean_source(value):
    return value if value and SOURCE_RE.fullmatch(value) else ""


def valid_token(value):
    return bool(value and TOKEN_RE.fullmatch(value))


def token_hash(token):
    # Only the hash is stored, so a copy of the table can't be used to confirm anyone.
    return hashlib.sha256(token.encode()).hexdigest()


def secret_matches(provided, secrets):
    """Constant-time check against the accepted secrets (several during a rotation). Works on bytes, so any
    header value, ASCII or not, is just a mismatch."""
    given = (provided or "").encode()
    return any(hmac.compare_digest(given, secret.encode()) for secret in secrets if secret)


def email_ref(email, key):
    # A short reference for logs, keyed so it can't be matched against a list of known addresses.
    return hmac.new(key.encode(), (email or "").encode(), hashlib.sha256).hexdigest()[:12]
