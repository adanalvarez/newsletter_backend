"""Tests for the send tool (tools/). They need the tool's packages: pip install -r tools/requirements.txt"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

try:
    import markdown  # noqa: F401
    from botocore.exceptions import ClientError

    import edition as editions
    import cli
    import render
except ImportError as missing:  # pragma: no cover
    raise unittest.SkipTest(f"tool packages not installed ({missing}); pip install -r tools/requirements.txt")

PUBLISHED = """---
title: "Sixty Seconds"
subject: "In 7 of 12 runs, an AI agent reached the data in about a minute"
description: "An AI attacker reached the data in about a minute. The logs arrived after that."
newsletter: "No Hype, No Fear"
edition: 3
date: "2026-11-15"
url: "https://adan.cloud/newsletter/sixty-seconds"
---

# Sixty Seconds

The story, with [a link](https://example.com) and **bold**.

![The clock](https://adan.cloud/newsletter/sixty-seconds/clock.png)

## If you decide

Ask your team one question.
"""

SOURCE = """---
title: Two Words                # the headline
subject: "The takeaway, with a number"   # the email subject line
description: "The first two lines."
date: 2026-11-20
edition: 4
type: research
draft: true                     # remove when ready
---

See [the research](/writing) and ![a chart](/newsletter/two-words/chart.png).
"""


def edition():
    return editions.parse(PUBLISHED, "sixty-seconds")


class EditionTest(unittest.TestCase):
    def test_published_format(self):
        e = edition()
        self.assertEqual((e.title, e.number, e.date, e.draft), ("Sixty Seconds", 3, "2026-11-15", False))
        self.assertTrue(e.body.startswith("The story"), "the repeated title heading is dropped")
        self.assertEqual(e.markdown_url, "https://adan.cloud/newsletter/sixty-seconds.md")

    def test_source_format_with_comments_and_relative_links(self):
        e = editions.parse(SOURCE, "two-words")
        self.assertEqual((e.title, e.subject, e.number, e.draft), ("Two Words", "The takeaway, with a number", 4, True))
        self.assertIn("(https://adan.cloud/writing)", e.body)
        self.assertIn("(https://adan.cloud/newsletter/two-words/chart.png)", e.body)
        self.assertEqual(e.url, "https://adan.cloud/newsletter/two-words")

    def test_missing_front_matter_is_an_error(self):
        with self.assertRaises(ValueError):
            editions.parse("no front matter", "x")
        with self.assertRaises(ValueError):
            editions.parse('---\ntitle: "Only a title"\n---\nbody', "x")

    def test_load_refuses_odd_sources(self):
        with self.assertRaises(ValueError):
            editions.load("../../etc/passwd")


class RenderTest(unittest.TestCase):
    def test_html_has_inline_styles_and_the_unsubscribe_placeholder(self):
        page = render.to_html(edition())
        self.assertIn("{{amazonSESUnsubscribeUrl}}", page)
        self.assertIn('<p style="margin:0 0 18px;">The story', page)
        self.assertIn('<h1 class="nl-title"', page)
        self.assertRegex(page, r'<img style="[^"]+" width="592" alt="The clock" src="https://adan.cloud/newsletter/sixty-seconds/clock.png"')
        self.assertIn('<a style="color:#0b6a87;text-decoration:underline;" href="https://example.com">a link</a>', page)
        self.assertIn("Edition 3 &middot; Nov 15, 2026 &middot; 1 min read", page)

    def test_decide_and_build_sections_become_callouts(self):
        page = render.to_html(edition())
        self.assertIn('class="nl-callout"', page)
        self.assertRegex(page, r'(?s)class="nl-callout".*?>If you decide</div>\s*<p[^>]*>Ask your team one question.</p>')
        self.assertNotIn(">If you decide</h2>", page)

    def test_dark_mode_and_accessibility(self):
        page = render.to_html(edition())
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn('role="article"', page)
        self.assertIn('<html lang="en">', page)

    def test_no_tracking(self):
        page = render.to_html(edition())
        images = [chunk.split('"')[0] for chunk in page.split('src="')[1:]]
        self.assertEqual(images, [render.LOGO, "https://adan.cloud/newsletter/sixty-seconds/clock.png"])

    def test_stays_under_gmails_clipping_size(self):
        self.assertLess(len(render.to_html(edition()).encode()), render.GMAIL_CLIP_BYTES)

    def test_title_and_description_are_escaped(self):
        e = edition()
        e.title = "<script>x</script>"
        self.assertNotIn("<script>", render.to_html(e))

    def test_reading_time_and_date(self):
        self.assertEqual(render.reading_minutes("word " * 460), 2)
        self.assertEqual(render.reading_minutes(""), 1)
        self.assertEqual(render.display_date("2026-11-05"), "Nov 5, 2026")

    def test_polls_become_anonymous_answer_links(self):
        e = edition()
        e.body += "\n\n```poll alert-speed\nHow long until a human sees your first alert?\n- Under 5 minutes\n- More than an hour\n```\n\nAfter the poll."
        page = render.to_html(e)
        self.assertIn("How long until a human sees your first alert?", page)
        self.assertIn('href="https://adan.cloud/newsletter/vote#e=sixty-seconds&amp;q=alert-speed&amp;a=1"', page)
        self.assertNotIn("NLPOLL", page)
        self.assertNotIn("```", page)
        self.assertIn("After the poll.", page)
        text = render.to_text(e)
        self.assertIn("POLL: How long until a human sees your first alert?", text)
        self.assertIn("  - More than an hour: https://adan.cloud/newsletter/vote#e=sixty-seconds&q=alert-speed&a=1", text)

    def test_every_edition_asks_if_it_was_useful(self):
        page = render.to_html(edition())
        self.assertIn("Was this edition useful?", page)
        for i in range(3):
            self.assertIn(f"#e=sixty-seconds&amp;q=useful&amp;a={i}", page)
        self.assertIn("Was this edition useful? Yes: https://adan.cloud/newsletter/vote#e=sixty-seconds&q=useful&a=0", render.to_text(edition()))

    def test_links_carry_no_reader_identity(self):
        page = render.to_html(edition())
        for href in [chunk.split('"')[0] for chunk in page.split('href="')[1:]]:
            self.assertNotRegex(href, r"[?&](email|uid|reader|r|id)=", href)

    def test_a_malformed_poll_is_an_error_not_a_broken_email(self):
        e = edition()
        e.body += "\n\n```poll broken\nOnly a question\n```"
        with self.assertRaises(ValueError):
            render.to_html(e)

    def test_text_version(self):
        text = render.to_text(edition())
        self.assertIn("a link (https://example.com)", text)
        self.assertIn("[Chart: The clock]", text)
        self.assertIn("Unsubscribe: {{amazonSESUnsubscribeUrl}}", text)
        self.assertIn("Reply to this email", text)
        self.assertNotIn("**", text)


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and Item["pk"] in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[Item["pk"]] = dict(Item)

    def update_item(self, Key, ExpressionAttributeValues, **_):
        self.items[Key["pk"]].update(status=ExpressionAttributeValues[":s"], message_id=ExpressionAttributeValues[":m"])

    def delete_item(self, Key):
        self.items.pop(Key["pk"], None)


class FakeSes:
    def __init__(self, readers, fail_for=()):
        self.readers = readers
        self.fail_for = set(fail_for)
        self.sent = []

    def list_contacts(self, **request):
        start = int(request.get("NextToken", 0))
        page = self.readers[start : start + 2]
        more = start + 2 < len(self.readers)
        return {"Contacts": [{"EmailAddress": a} for a in page], **({"NextToken": str(start + 2)} if more else {})}

    def send_email(self, **request):
        to = request["Destination"]["ToAddresses"][0]
        if to in self.fail_for:
            raise ClientError({"Error": {"Code": "Throttling"}}, "SendEmail")
        self.sent.append(request)
        return {"MessageId": f"id-{len(self.sent)}"}


class SendTest(unittest.TestCase):
    def setUp(self):
        self._fetch = editions.fetch
        editions.fetch = lambda slug, site=editions.SITE: edition()
        self.args = SimpleNamespace(slug="sixty-seconds", rate=1000, yes=False)
        self.output = []

    def tearDown(self):
        editions.fetch = self._fetch

    def run_send(self, ses, table, answer="sixty-seconds"):
        cli.send(self.args, ses=ses, table=table, confirm=lambda _: answer, sleep=lambda _: None, out=self.output.append)

    def test_sends_once_to_each_reader_through_the_contact_list(self):
        ses, table = FakeSes(["a@example.com", "b@example.com", "c@example.com"]), FakeTable()
        self.run_send(ses, table)
        self.assertEqual(len(ses.sent), 3)
        first = ses.sent[0]
        self.assertEqual(first["ListManagementOptions"], {"ContactListName": "newsletter", "TopicName": "newsletter"})
        self.assertEqual(first["EmailTags"], [{"Name": "edition", "Value": "sixty-seconds"}])
        self.assertEqual(first["ConfigurationSetName"], "newsletter")
        self.assertTrue(first["FromEmailAddress"].startswith("=?utf-8?"), "non-ASCII sender name is encoded")
        # Re-running sends nothing new.
        self.run_send(ses, table)
        self.assertEqual(len(ses.sent), 3)
        self.assertIn("Sent 0, already had it 3, failed 0.", self.output)

    def test_wrong_confirmation_sends_nothing(self):
        ses = FakeSes(["a@example.com"])
        self.run_send(ses, FakeTable(), answer="yes")
        self.assertEqual(ses.sent, [])

    def test_a_failure_is_released_so_the_next_run_retries_it(self):
        ses, table = FakeSes(["a@example.com", "b@example.com"], fail_for=["b@example.com"]), FakeTable()
        self.run_send(ses, table)
        self.assertEqual(len(ses.sent), 1)
        ses.fail_for.clear()
        self.run_send(ses, table)
        self.assertEqual([r["Destination"]["ToAddresses"][0] for r in ses.sent], ["a@example.com", "b@example.com"])

    def test_variant_addresses_of_one_inbox_get_one_copy(self):
        ses = FakeSes(["reader@gmail.com", "re.ader+news@gmail.com"])
        self.run_send(ses, FakeTable())
        self.assertEqual(len(ses.sent), 1)

    def test_drafts_are_never_sent(self):
        editions.fetch = lambda slug, site=editions.SITE: editions.parse(SOURCE, "two-words")
        with self.assertRaises(SystemExit):
            self.run_send(FakeSes(["a@example.com"]), FakeTable())


if __name__ == "__main__":
    unittest.main()
