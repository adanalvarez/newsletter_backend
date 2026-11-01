"""Anonymous answers to polls and to "Was this edition useful?": POST /api/vote.

Nothing identifies the reader. No email, no reader ID in the links, no IP address stored. To stop one person
voting many times, the vote stores a one-way code made from the IP address, the question and the day with a
secret key. The code changes every day and is deleted within 26 hours, so it can't follow anyone over time.
A comment, if the reader adds one, is stored with the answer only, and deleted after a year.
"""

import hashlib
import hmac
import logging
import time

from . import config, web
from .clock import day
from .polls import Polls, from_s3
from .validation import clean_comment, valid_id
from .web import redirect, text

logger = logging.getLogger()
logger.setLevel(logging.INFO)

THANKS = "/newsletter/thanks-for-your-answer"


class Deps:
    def __init__(self, settings, store, polls, now=lambda: int(time.time())):
        self.settings = settings
        self.store = store
        self.polls = polls
        self.now = now


_deps = None


def lambda_handler(event, context):
    global _deps
    if _deps is None:
        import boto3

        from .store import Store

        settings = config.load()
        table = boto3.resource("dynamodb").Table(settings.table_name)
        polls = Polls(from_s3(boto3.client("s3"), settings.polls_bucket, settings.polls_key))
        _deps = Deps(settings, Store(table, settings), polls)
    return handle(event, _deps)


def handle(event, deps):
    settings = deps.settings
    if web.refused(event, settings):
        return text(403, "Forbidden")
    if event.get("rawPath") != "/api/vote" or web.method(event) != "POST":
        return text(404, "Not found")

    fields = web.form(event)
    if fields.get("website"):  # the honeypot
        return redirect(settings, THANKS)

    edition, question, answer = fields.get("e", ""), fields.get("q", ""), fields.get("a", "")
    if not (valid_id(edition) and valid_id(question) and answer.isdigit() and len(answer) == 1):
        return text(400, "That answer doesn't look right. Please go back and try again.")
    try:
        options = deps.polls.options(edition, question)
    except Exception:
        logger.exception("vote: couldn't read the list of open questions")
        return text(503, "Answers can't be saved right now. Please try again in a few minutes.")
    if not options or int(answer) >= len(options):
        return text(400, "That question isn't open.")

    now = deps.now()
    voter = voter_code(web.headers(event).get("cf-connecting-ip", ""), edition, question, now, settings.vote_key)
    if deps.store.record_vote(edition, question, int(answer), voter, now):
        comment = clean_comment(fields.get("comment"))
        if comment:
            deps.store.add_comment(edition, question, int(answer), comment, now)
        logger.info("vote: %s/%s counted", edition, question)
    else:
        logger.info("vote: %s/%s repeat from the same reader today, not counted", edition, question)
    return redirect(settings, THANKS)


def voter_code(ip, edition, question, now, key):
    message = f"{day(now)}|{edition}|{question}|{ip}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()[:32]
