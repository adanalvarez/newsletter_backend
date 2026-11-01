"""The questions readers can answer, from the list the site publishes (newsletter/polls.json, built from the
editions). Only real editions, questions and answers are counted, so nobody can create counters at will.

polls.json:
    {"useful": {"question": "...", "options": ["Yes", "Somewhat", "No"]},
     "editions": {"<slug>": {"title": "...", "polls": {"<id>": {"question": "...", "options": [...]}}}}}

"useful" ("Was this edition useful?") is open for every edition. The list is read from the site's S3 bucket,
not through adan.cloud, so Cloudflare's bot protection never stands in the way. It's cached for a few minutes.
"""

import json
import time

USEFUL = "useful"


class Polls:
    def __init__(self, fetch, ttl=300, clock=time.monotonic):
        self.fetch = fetch  # returns the parsed polls.json
        self.ttl = ttl
        self.clock = clock
        self.data = None
        self.loaded_at = 0.0

    def current(self):
        if self.data is None or self.clock() - self.loaded_at > self.ttl:
            self.data = self.fetch()
            self.loaded_at = self.clock()
        return self.data

    def options(self, edition, question):
        """The answers for this question, or None if there's no such open question."""
        data = self.current()
        if edition not in data.get("editions", {}):
            return None
        if question == USEFUL:
            return data.get(USEFUL, {}).get("options")
        return data["editions"][edition].get("polls", {}).get(question, {}).get("options")


def from_s3(s3, bucket, key):
    def fetch():
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    return fetch
