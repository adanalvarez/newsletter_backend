"""What every /api handler shares: the checks each request must pass, reading the form, and the answers.

A request is answered only if it came through Cloudflare (X-Edge-Verify) and the site's CloudFront
(X-Origin-Verify), and, for a POST, from a page on the site itself (Origin), so forms on other sites can't
post here for their visitors.
"""

import base64
from urllib.parse import parse_qs

from .validation import secret_matches

MAX_BODY = 4096


def headers(event):
    return {key.lower(): value for key, value in (event.get("headers") or {}).items()}


def method(event):
    return event.get("requestContext", {}).get("http", {}).get("method", "")


def refused(event, settings):
    """True if the request must get a 403."""
    sent = headers(event)
    if not secret_matches(sent.get("x-origin-verify"), settings.origin_secrets):
        return True
    if settings.edge_secrets and not secret_matches(sent.get("x-edge-verify"), settings.edge_secrets):
        return True
    return method(event) == "POST" and not same_site(sent, settings.site_url)


def same_site(sent, site_url):
    origin = sent.get("origin")
    if origin:
        return origin == site_url
    return sent.get("sec-fetch-site") == "same-origin"


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
