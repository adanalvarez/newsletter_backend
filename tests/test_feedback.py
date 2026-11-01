import unittest
from urllib.parse import urlencode

from fakes import NOW, SETTINGS, FakeStore

from newsletter import feedback
from newsletter.polls import Polls

SITE = "https://adan.cloud"
HEADERS = {"X-Origin-Verify": "s3cret", "X-Edge-Verify": "3dge", "Origin": SITE, "CF-Connecting-IP": "203.0.113.7"}
POLLS = {
    "useful": {"question": "Was this edition useful?", "options": ["Yes", "Somewhat", "No"]},
    "editions": {
        "sixty-seconds": {"title": "Sixty Seconds", "polls": {"alert-speed": {"question": "How fast?", "options": ["<5 min", "5-60 min", ">1 h"]}}},
        "no-polls": {"title": "No polls", "polls": {}},
    },
}


def vote(fields, headers=None, path="/api/vote", method="POST"):
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": HEADERS if headers is None else headers,
        "body": urlencode(fields),
        "isBase64Encoded": False,
    }


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.fetches = 0

        def fetch():
            self.fetches += 1
            return POLLS

        self.now = NOW
        self.deps = feedback.Deps(SETTINGS, self.store, Polls(fetch), now=lambda: self.now)

    def call(self, fields, **kwargs):
        return feedback.handle(vote(fields, **kwargs), self.deps)

    def test_an_answer_is_counted_anonymously(self):
        response = self.call({"e": "sixty-seconds", "q": "alert-speed", "a": "1"})
        self.assertEqual(response["headers"]["Location"], f"{SITE}/newsletter/thanks-for-your-answer")
        self.assertEqual(self.store.polls, {("sixty-seconds", "alert-speed"): {1: 1}})
        voter = next(iter(self.store.voters))[2]
        self.assertNotIn("203.0.113.7", voter)
        self.assertEqual(len(voter), 32)

    def test_useful_is_open_for_every_edition(self):
        self.call({"e": "no-polls", "q": "useful", "a": "0"})
        self.assertEqual(self.store.polls, {("no-polls", "useful"): {0: 1}})

    def test_one_vote_per_reader_per_question_per_day(self):
        for answer in ("0", "2", "1"):
            self.call({"e": "sixty-seconds", "q": "alert-speed", "a": answer})
        self.assertEqual(self.store.polls[("sixty-seconds", "alert-speed")], {0: 1})
        # Another reader counts; the same reader counts again the next day.
        self.call({"e": "sixty-seconds", "q": "alert-speed", "a": "2"}, headers={**HEADERS, "CF-Connecting-IP": "198.51.100.2"})
        self.now += 86400
        self.call({"e": "sixty-seconds", "q": "alert-speed", "a": "2"})
        self.assertEqual(self.store.polls[("sixty-seconds", "alert-speed")], {0: 1, 2: 2})

    def test_the_voter_code_changes_every_day(self):
        today = feedback.voter_code("203.0.113.7", "e", "q", NOW, "k")
        tomorrow = feedback.voter_code("203.0.113.7", "e", "q", NOW + 86400, "k")
        self.assertNotEqual(today, tomorrow)

    def test_comment_is_kept_cleaned_and_only_with_a_counted_vote(self):
        self.call({"e": "sixty-seconds", "q": "useful", "a": "0", "comment": "Great\x1b[2J one‮!"})
        self.call({"e": "sixty-seconds", "q": "useful", "a": "0", "comment": "spam again"})
        self.assertEqual(self.store.comments, [("sixty-seconds", "useful", 0, "Great[2J one!")])

    def test_only_real_questions_and_answers_count(self):
        for fields in (
            {"e": "unknown-edition", "q": "useful", "a": "0"},
            {"e": "sixty-seconds", "q": "made-up", "a": "0"},
            {"e": "sixty-seconds", "q": "alert-speed", "a": "3"},
            {"e": "sixty-seconds", "q": "alert-speed", "a": "-1"},
            {"e": "../x", "q": "useful", "a": "0"},
            {"e": "sixty-seconds", "q": "useful", "a": "10"},
        ):
            self.assertEqual(self.call(fields)["statusCode"], 400, fields)
        self.assertEqual(self.store.polls, {})

    def test_honeypot_gets_thanked_and_nothing_else(self):
        self.call({"e": "sixty-seconds", "q": "useful", "a": "0", "website": "x"})
        self.assertEqual(self.store.polls, {})

    def test_requests_that_skip_the_site_or_come_from_elsewhere_are_refused(self):
        for headers in (
            {**HEADERS, "X-Edge-Verify": "nope"},
            {**HEADERS, "Origin": "https://evil.example"},
            {"Origin": SITE},
        ):
            self.assertEqual(self.call({"e": "sixty-seconds", "q": "useful", "a": "0"}, headers=headers)["statusCode"], 403)
        self.assertEqual(self.call({}, path="/api/vote", method="GET")["statusCode"], 404)

    def test_question_list_is_cached(self):
        for _ in range(3):
            self.call({"e": "sixty-seconds", "q": "useful", "a": "0"}, headers={**HEADERS, "CF-Connecting-IP": str(_)})
        self.assertEqual(self.fetches, 1)

    def test_if_the_question_list_cant_be_read_the_reader_is_told_to_retry(self):
        def broken():
            raise OSError("S3 down")

        self.deps.polls = Polls(broken)
        self.assertEqual(self.call({"e": "sixty-seconds", "q": "useful", "a": "0"})["statusCode"], 503)


if __name__ == "__main__":
    unittest.main()
