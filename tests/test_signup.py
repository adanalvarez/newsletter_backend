import base64
import unittest
from urllib.parse import urlencode

from fakes import NOW, SETTINGS, FakeMailer, FakeStore

from newsletter import signup
from newsletter.validation import canonical, normalize_email, token_hash

SITE = "https://adan.cloud"
VALID_HEADERS = {"X-Origin-Verify": "s3cret", "X-Edge-Verify": "3dge", "Origin": SITE}


def request(path, method="POST", fields=None, query=None, headers=None, b64=False, body=None):
    body = urlencode(fields or {}) if body is None else body
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": {"Content-Type": "application/x-www-form-urlencoded", **(VALID_HEADERS if headers is None else headers)},
        "queryStringParameters": query,
        "body": base64.b64encode(body.encode()).decode() if b64 else body,
        "isBase64Encoded": b64,
    }


class FakeClock:
    """Monotonic clock for the response padding; sleeping just moves it forward."""

    def __init__(self, step=0.0):
        self.t = 100.0
        self.step = step  # how long each call "takes"
        self.slept = []

    def monotonic(self):
        self.t += self.step
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class SignupTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.mailer = FakeMailer()
        self.now = NOW
        self.clock = FakeClock()
        self.deps = signup.Deps(SETTINGS, self.store, self.mailer, now=lambda: self.now, clock=self.clock.monotonic, sleep=self.clock.sleep)

    def call(self, *args, **kwargs):
        return signup.handle(request(*args, **kwargs), self.deps)

    def subscribe(self, email="Reader@Example.com", **extra):
        fields = {"email": email, "role": "Other", "source": "/newsletter", "consent_version": "2026-11-01", **extra}
        return self.call("/api/subscribe", fields=fields)

    def sent_token(self):
        return self.mailer.sent[-1][1].split("#t=", 1)[1]

    def location(self, response):
        return response["headers"]["Location"]

    # --- who may call ---

    def test_requests_without_both_secrets_are_refused(self):
        for headers in (
            {"Origin": SITE},
            {"X-Origin-Verify": "wrong", "X-Edge-Verify": "3dge", "Origin": SITE},
            {"X-Origin-Verify": "s3cret", "Origin": SITE},  # skipped Cloudflare
            {"X-Origin-Verify": "s3cret", "X-Edge-Verify": "nope", "Origin": SITE},
        ):
            response = self.call("/api/subscribe", fields={"email": "a@b.co"}, headers=headers)
            self.assertEqual(response["statusCode"], 403, headers)
        self.assertEqual(self.mailer.sent, [])

    def test_a_rotated_secret_is_accepted(self):
        headers = {**VALID_HEADERS, "X-Origin-Verify": "n3w-s3cret"}
        self.assertEqual(self.call("/api/subscribe", fields={"email": "a@b.co"}, headers=headers)["statusCode"], 303)

    def test_non_ascii_secret_header_is_a_mismatch_not_a_crash(self):
        headers = {**VALID_HEADERS, "X-Origin-Verify": "sécret"}
        self.assertEqual(self.call("/api/subscribe", fields={"email": "a@b.co"}, headers=headers)["statusCode"], 403)

    def test_posts_from_other_sites_are_refused(self):
        for headers in (
            {**VALID_HEADERS, "Origin": "https://evil.example"},
            {"X-Origin-Verify": "s3cret", "X-Edge-Verify": "3dge"},  # no Origin and no Sec-Fetch-Site
            {"X-Origin-Verify": "s3cret", "X-Edge-Verify": "3dge", "Sec-Fetch-Site": "cross-site"},
        ):
            self.assertEqual(self.call("/api/subscribe", fields={"email": "a@b.co"}, headers=headers)["statusCode"], 403)
        self.assertEqual(self.mailer.sent, [])

    def test_same_origin_without_origin_header_is_accepted(self):
        headers = {"X-Origin-Verify": "s3cret", "X-Edge-Verify": "3dge", "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.call("/api/subscribe", fields={"email": "a@b.co"}, headers=headers)["statusCode"], 303)

    def test_unknown_routes_are_not_found(self):
        self.assertEqual(self.call("/api/other")["statusCode"], 404)

    # --- subscribe ---

    def test_new_sign_up_is_pending_and_gets_a_confirmation_link(self):
        response = self.subscribe()
        self.assertEqual(self.location(response), f"{SITE}/newsletter/check-your-inbox")
        subscriber = self.store.subscribers["reader@example.com"]
        self.assertEqual((subscriber["status"], subscriber["role"], subscriber["consent_version"]), ("pending", "Other", "2026-11-01"))
        email, link = self.mailer.sent[0]
        self.assertEqual(email, "reader@example.com")
        # The code is after "#", so it never reaches server logs or a Referer header.
        self.assertTrue(link.startswith(f"{SITE}/newsletter/confirm#t="))
        self.assertIn(token_hash(self.sent_token()), self.store.tokens)
        self.assertNotIn(self.sent_token(), self.store.tokens)

    def test_every_answer_takes_about_the_same_time(self):
        self.subscribe()  # sends an email
        self.subscribe()  # nothing sent (recently pending)
        self.assertEqual(len(self.clock.slept), 2)
        self.assertTrue(all(0.7 <= s <= 0.85 for s in self.clock.slept), self.clock.slept)

    def test_slow_paths_are_not_padded_further(self):
        self.clock.step = 1.0  # every step takes a second: already slower than the target
        self.subscribe()
        self.assertEqual(self.clock.slept, [])

    def test_form_body_can_arrive_base64_encoded(self):
        self.call("/api/subscribe", fields={"email": "b64@example.com"}, b64=True)
        self.assertEqual(self.mailer.sent[0][0], "b64@example.com")

    def test_oversized_or_garbled_bodies_are_ignored(self):
        self.assertEqual(self.call("/api/subscribe", body="email=" + "a" * 5000)["statusCode"], 400)
        self.assertEqual(self.call("/api/subscribe", body="&".join(f"f{i}=x" for i in range(50)))["statusCode"], 400)
        event = request("/api/subscribe")
        event.update(body="%%%not-base64", isBase64Encoded=True)
        self.assertEqual(signup.handle(event, self.deps)["statusCode"], 400)

    def test_unknown_role_odd_source_and_unknown_consent_version_are_not_recorded(self):
        self.subscribe(role="Budget owner", source="javascript:alert(1)", consent_version="1999-01-01")
        subscriber = self.store.subscribers["reader@example.com"]
        self.assertEqual((subscriber["role"], subscriber["source"], subscriber["consent_version"]), ("", "", "2026-11-01"))

    def test_an_older_known_consent_version_is_recorded_as_shown(self):
        self.subscribe(consent_version="2026-06-01")
        self.assertEqual(self.store.subscribers["reader@example.com"]["consent_version"], "2026-06-01")

    def test_honeypot_gets_the_normal_answer_and_nothing_else(self):
        response = self.subscribe(website="http://spam.example")
        self.assertEqual(response["statusCode"], 303)
        self.assertEqual((self.mailer.sent, self.store.subscribers), ([], {}))

    def test_invalid_email_is_rejected(self):
        for bad in [
            "",
            "no-at-sign",
            "a@b",
            "a b@example.com",
            "<x>@example.com",
            "x" * 250 + "@example.com",
            "a@-.co",
            "a@ex..com",
            "a\x00b@example.com",
            "ñame@example.com",
            "a(b)@example.com",
            "a@b@example.com",
        ]:
            self.assertEqual(self.subscribe(email=bad)["statusCode"], 400, repr(bad))
        self.assertEqual(self.mailer.sent, [])

    def test_already_confirmed_gets_the_same_answer_and_no_email(self):
        self.store.subscribers["reader@example.com"] = {"status": "confirmed"}
        self.assertEqual(self.location(self.subscribe()), f"{SITE}/newsletter/check-your-inbox")
        self.assertEqual(self.mailer.sent, [])

    def test_bounced_or_complained_addresses_are_never_emailed_again(self):
        for status in ("bounced", "complained"):
            self.store.subscribers["reader@example.com"] = {"status": status}
            self.subscribe()
        self.assertEqual(self.mailer.sent, [])

    def test_one_inbox_gets_one_confirmation_a_day_whatever_the_variant(self):
        self.subscribe(email="victim@gmail.com")
        for variant in ("victim+1@gmail.com", "v.i.c.t.i.m@gmail.com", "Victim+x@googlemail.com"):
            self.subscribe(email=variant)
        self.assertEqual(len(self.mailer.sent), 1)
        self.now += 25 * 3600
        self.subscribe(email="victim+2@gmail.com")
        self.assertEqual(len(self.mailer.sent), 2)
        # The email goes to the address as typed.
        self.assertEqual(self.mailer.sent[1][0], "victim+2@gmail.com")

    def test_daily_ceiling_stops_confirmation_emails(self):
        for i in range(3):
            self.subscribe(email=f"reader{i}@example.com")
        self.assertEqual(len(self.mailer.sent), 2)  # the test ceiling is 2

    def test_someone_who_unsubscribed_can_sign_up_again(self):
        self.store.subscribers["reader@example.com"] = {"status": "unsubscribed", "history": [("unsubscribed", "link")]}
        self.subscribe()
        self.assertEqual(len(self.mailer.sent), 1)
        # Their earlier history is kept.
        self.assertEqual(self.store.subscribers["reader@example.com"]["history"], [("unsubscribed", "link")])

    def test_address_ses_refuses_gets_the_normal_answer_not_an_error(self):
        self.deps.mailer = FakeMailer(reject=True)
        response = self.subscribe()
        self.assertEqual(self.location(response), f"{SITE}/newsletter/check-your-inbox")

    # --- confirm ---

    def test_confirming_subscribes_the_contact_and_uses_up_the_link(self):
        self.subscribe()
        token = self.sent_token()
        response = self.call("/api/confirm", fields={"t": token})
        self.assertEqual(self.location(response), f"{SITE}/newsletter/confirmed")
        subscriber = self.store.subscribers["reader@example.com"]
        self.assertEqual(subscriber["status"], "confirmed")
        self.assertEqual(subscriber["history"], [("confirmed", "2026-11-01", "/newsletter")])
        self.assertEqual(self.mailer.contacts["reader@example.com"]["source"], "/newsletter")
        # A second use of the same link doesn't work.
        self.assertEqual(self.location(self.call("/api/confirm", fields={"t": token})), f"{SITE}/newsletter/link-expired")

    def test_confirming_a_variant_sign_up_subscribes_the_address_as_typed(self):
        self.subscribe(email="reader+news@example.com")
        self.call("/api/confirm", fields={"t": self.sent_token()})
        self.assertIn("reader+news@example.com", self.mailer.contacts)
        self.assertEqual(self.store.subscribers["reader@example.com"]["status"], "confirmed")

    def test_if_ses_fails_while_confirming_the_link_still_works_afterwards(self):
        self.subscribe()
        token = self.sent_token()
        self.deps.mailer = FakeMailer(fail_contact=True)
        with self.assertRaises(RuntimeError):
            self.call("/api/confirm", fields={"t": token})
        self.deps.mailer = self.mailer
        self.assertEqual(self.location(self.call("/api/confirm", fields={"t": token})), f"{SITE}/newsletter/confirmed")

    def test_expired_link_does_not_confirm(self):
        self.subscribe()
        token = self.sent_token()
        self.now += 49 * 3600
        self.assertEqual(self.location(self.call("/api/confirm", fields={"t": token})), f"{SITE}/newsletter/link-expired")
        self.assertEqual(self.mailer.contacts, {})

    def test_malformed_token_does_not_confirm(self):
        for bad in ("../../etc", "a" * 40 + "\n", ""):
            self.assertEqual(self.location(self.call("/api/confirm", fields={"t": bad})), f"{SITE}/newsletter/link-expired")

    def test_confirm_post_from_another_site_is_refused(self):
        self.subscribe()
        headers = {**VALID_HEADERS, "Origin": "https://evil.example"}
        self.assertEqual(self.call("/api/confirm", fields={"t": self.sent_token()}, headers=headers)["statusCode"], 403)

    def test_get_on_the_confirm_link_never_confirms(self):
        # Link scanners follow GET links; only the button on the page (a POST) confirms.
        self.subscribe()
        token = self.sent_token()
        response = self.call("/api/confirm", method="GET", query={"t": token})
        self.assertEqual(self.location(response), f"{SITE}/newsletter/confirm#t={token}")
        self.assertEqual(self.store.subscribers["reader@example.com"]["status"], "pending")

    def test_get_with_a_bad_token_cannot_inject_into_the_redirect(self):
        response = self.call("/api/confirm", method="GET", query={"t": "x" * 40 + "\r\nSet-Cookie: a=b"})
        self.assertEqual(self.location(response), f"{SITE}/newsletter/confirm")


class ValidationTest(unittest.TestCase):
    def test_canonical_inbox(self):
        self.assertEqual(canonical("a.b+tag@gmail.com"), "ab@gmail.com")
        self.assertEqual(canonical("a.b@googlemail.com"), "ab@gmail.com")
        self.assertEqual(canonical("a.b+tag@example.com"), "a.b@example.com")
        self.assertEqual(canonical("+only@example.com"), "+only@example.com")

    def test_normalize_keeps_valid_addresses(self):
        for good in ("o'brien@example.com", "first.last@sub.example.co.uk", "x+y@example.io"):
            self.assertEqual(normalize_email(good), good)
        # Surrounding whitespace (a stray newline from a paste) is trimmed, not rejected.
        self.assertEqual(normalize_email("  A@Example.com\n"), "a@example.com")


if __name__ == "__main__":
    unittest.main()
