"""The newsletter's command line: preview, test, send, and see how an edition did.

    python tools/cli.py preview <slug | path/to/edition.md>   # writes the email to build/preview/ to open
    python tools/cli.py test <slug | path/to/edition.md>      # sends it to you only (default adan@adan.cloud)
    python tools/cli.py send <slug>                           # sends the published edition to every reader
    python tools/cli.py stats [<slug>]                        # one edition's numbers, or the subscriber totals

`send` only takes a slug: it reads the published edition from adan.cloud, so the email is exactly what's live.
It asks you to type the slug before sending, never emails anyone twice (re-running it is safe), and skips
readers who already got this edition.

Runs with your own AWS credentials, in eu-west-1. Settings can be changed with environment variables (below).
"""

import argparse
import os
import sys
import time
import webbrowser
from collections import Counter
from email.utils import formataddr, parseaddr
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambda_code"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import edition as editions  # noqa: E402
import render  # noqa: E402
from newsletter.validation import canonical, clean_comment  # noqa: E402

REGION = os.environ.get("AWS_REGION", "eu-west-1")
TABLE = os.environ.get("NEWSLETTER_TABLE", "newsletter")
CONTACT_LIST = os.environ.get("NEWSLETTER_CONTACT_LIST", "newsletter")
TOPIC = os.environ.get("NEWSLETTER_TOPIC", "newsletter")
CONFIGURATION_SET = os.environ.get("NEWSLETTER_CONFIGURATION_SET", "newsletter")
FROM = os.environ.get("NEWSLETTER_FROM", "Adan Álvarez <adan@adan.cloud>")
TEST_TO = os.environ.get("NEWSLETTER_TEST_TO", "adan@adan.cloud")
POSTAL = os.environ.get("NEWSLETTER_POSTAL", "Barcelona, Spain")
SENT_DAYS = 180


def sender():
    name, address = parseaddr(FROM)
    return formataddr((name, address), charset="utf-8")


def message(edition, unsubscribe, subject_prefix=""):
    return {
        "Simple": {
            "Subject": {"Data": subject_prefix + edition.subject, "Charset": "UTF-8"},
            "Body": {
                "Html": {"Data": render.to_html(edition, postal=POSTAL, unsubscribe=unsubscribe), "Charset": "UTF-8"},
                "Text": {"Data": render.to_text(edition, postal=POSTAL, unsubscribe=unsubscribe), "Charset": "UTF-8"},
            },
        }
    }


# ---- preview and test ----


def preview(args):
    edition = editions.load(args.source)
    out = ROOT / "build" / "preview"
    out.mkdir(parents=True, exist_ok=True)
    page = out / f"{edition.slug}.html"
    page.write_text(render.to_html(edition, postal=POSTAL, unsubscribe=render.PREVIEW_UNSUBSCRIBE), encoding="utf-8")
    (out / f"{edition.slug}.txt").write_text(render.to_text(edition, postal=POSTAL, unsubscribe=render.PREVIEW_UNSUBSCRIBE), encoding="utf-8")
    print(f"Subject: {edition.subject}\nPreview: {page}\nText:    {page.with_suffix('.txt')}")
    size = len(page.read_bytes())
    if size > render.GMAIL_CLIP_BYTES * 0.9:
        print(f"Warning: the email is {size // 1000} KB. Gmail cuts emails off at about 102 KB; shorten it or split it.")
    if not args.no_open:
        webbrowser.open(page.as_uri())


def test(args):
    edition = editions.load(args.source)
    ses = boto3.client("sesv2", region_name=REGION)
    # A test isn't sent through the contact list, so it has no real unsubscribe link and isn't counted.
    ses.send_email(
        FromEmailAddress=sender(),
        Destination={"ToAddresses": [args.to]},
        Content=message(edition, render.PREVIEW_UNSUBSCRIBE, subject_prefix="[TEST] "),
    )
    print(f"Test of '{edition.subject}' sent to {args.to}.")


# ---- send ----


def readers(ses):
    """Everyone opted in to the newsletter topic."""
    found, token = [], None
    while True:
        request = {
            "ContactListName": CONTACT_LIST,
            "Filter": {"FilteredStatus": "OPT_IN", "TopicFilter": {"TopicName": TOPIC, "UseDefaultIfPreferenceUnavailable": False}},
            "PageSize": 1000,
        }
        if token:
            request["NextToken"] = token
        page = ses.list_contacts(**request)
        found += [contact["EmailAddress"] for contact in page.get("Contacts", [])]
        token = page.get("NextToken")
        if not token:
            return found


def send(args, ses=None, table=None, confirm=input, sleep=time.sleep, out=print):
    ses = ses or boto3.client("sesv2", region_name=REGION)
    table = table or boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    edition = editions.fetch(args.slug)
    if edition.draft:
        sys.exit("That edition is still a draft on the site. Publish it first.")

    recipients = readers(ses)
    out(f"Edition {edition.number}: {edition.title}\nSubject: {edition.subject}\nFrom:    {FROM}\nReaders: {len(recipients)}")
    if not recipients:
        return
    if not args.yes and confirm(f"Type the slug ({edition.slug}) to send it to {len(recipients)} readers: ").strip() != edition.slug:
        out("Not sent.")
        return

    content = message(edition, render.UNSUBSCRIBE)
    sent = skipped = failed = 0
    now = int(time.time())
    for address in recipients:
        key = f"SENT#{edition.slug}#{canonical(address.lower())}"
        try:
            # Claim this reader for this edition first, so a re-run (or a second terminal) can't send twice.
            table.put_item(
                Item={"pk": key, "status": "sending", "at": now, "expires_at": now + SENT_DAYS * 86400},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                skipped += 1
                continue
            raise
        try:
            response = ses.send_email(
                FromEmailAddress=sender(),
                Destination={"ToAddresses": [address]},
                Content=content,
                ConfigurationSetName=CONFIGURATION_SET,
                # Through the contact list: SES adds the one-click unsubscribe headers and fills in the link.
                ListManagementOptions={"ContactListName": CONTACT_LIST, "TopicName": TOPIC},
                EmailTags=[{"Name": "edition", "Value": edition.slug}],
            )
            table.update_item(
                Key={"pk": key},
                UpdateExpression="SET #s = :s, message_id = :m",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "sent", ":m": response["MessageId"]},
            )
            sent += 1
        except ClientError as error:
            # Release the claim so the next run retries this reader.
            table.delete_item(Key={"pk": key})
            failed += 1
            out(f"  failed: {error.response['Error']['Code']}")
        sleep(1 / args.rate)
    out(f"Sent {sent}, already had it {skipped}, failed {failed}." + (" Re-run the same command to retry the failures." if failed else ""))


# ---- stats ----


def stats(args):
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    if args.slug:
        numbers = table.get_item(Key={"pk": f"EDITION#{args.slug}"}).get("Item") or {}
        sent = int(numbers.get("sent", 0))
        print(f"Edition '{args.slug}' (counts arrive within minutes of sending)")
        for field in ("sent", "delivered", "bounced", "complained", "unsubscribed"):
            value = int(numbers.get(field, 0))
            share = f"  ({value / sent:.1%})" if sent and field != "sent" else ""
            print(f"  {field:<13}{value:>6}{share}")
        print("  Opens and clicks aren't tracked (no tracking pixels). Look at replies, and at visits to the")
        print("  edition's page in CloudFront's \"Popular objects\" report.")
        answers(table, args.slug)
        return

    statuses, roles, sources = Counter(), Counter(), Counter()
    scan = {"FilterExpression": "begins_with(pk, :p)", "ExpressionAttributeValues": {":p": "SUB#"}}
    while True:
        page = table.scan(**scan)
        for item in page["Items"]:
            statuses[item.get("status", "?")] += 1
            if item.get("status") == "confirmed":
                roles[item.get("role") or "(not given)"] += 1
                sources[item.get("source") or "(unknown)"] += 1
        if "LastEvaluatedKey" not in page:
            break
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    print("Subscribers by status:", dict(statuses))
    print("Confirmed, by role:   ", dict(roles.most_common()))
    print("Confirmed, by page:   ", dict(sources.most_common()))


def scan_prefix(table, prefix):
    items, request = [], {"FilterExpression": "begins_with(pk, :p)", "ExpressionAttributeValues": {":p": prefix}}
    while True:
        page = table.scan(**request)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        request["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def answers(table, slug):
    """Poll results, the "useful" score and anonymous comments for one edition."""
    try:
        questions = editions.fetch_polls().get("editions", {}).get(slug, {}).get("polls", {})
    except Exception:
        questions = {}  # labels are a nicety; counts still print
    questions = {"useful": {"question": render.USEFUL_QUESTION, "options": render.USEFUL_OPTIONS}, **questions}
    for item in sorted(scan_prefix(table, f"POLL#{slug}#"), key=lambda i: i["pk"]):
        question_id = item["pk"].split("#", 2)[2]
        question = questions.get(question_id, {"question": question_id, "options": []})
        total = int(item.get("total", 0))
        print(f"\n  {question['question']}  ({total} answers)")
        for i in range(10):
            if f"a{i}" in item or i < len(question["options"]):
                count = int(item.get(f"a{i}", 0))
                label = question["options"][i] if i < len(question["options"]) else f"answer {i}"
                print(f"    {label:<36}{count:>5}  {count / total:.0%}" if total else f"    {label:<36}{count:>5}")
    comments = sorted(scan_prefix(table, f"COMMENT#{slug}#"), key=lambda i: i["pk"])
    if comments:
        print("\n  Comments (anonymous):")
        for item in comments:
            question_id = item["pk"].split("#")[2]
            options = questions.get(question_id, {}).get("options", [])
            answer = int(item.get("answer", 0))
            label = options[answer] if answer < len(options) else f"answer {answer}"
            # Cleaned again on the way out: a comment can't send escape codes to your terminal.
            print(f"    [{question_id}: {label}] {clean_comment(item.get('comment', ''))}")


def main():
    parser = argparse.ArgumentParser(description="No Hype, No Fear: preview, test, send and stats.")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("preview", help="write the email to build/preview/ and open it")
    p.add_argument("source", help="edition slug (live site) or path to a local .md file")
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(run=preview)
    p = commands.add_parser("test", help="send the email to yourself only")
    p.add_argument("source", help="edition slug (live site) or path to a local .md file")
    p.add_argument("--to", default=TEST_TO)
    p.set_defaults(run=test)
    p = commands.add_parser("send", help="send the published edition to every reader")
    p.add_argument("slug")
    p.add_argument("--rate", type=float, default=5, help="emails per second (default 5)")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(run=send)
    p = commands.add_parser("stats", help="an edition's numbers, or subscriber totals without a slug")
    p.add_argument("slug", nargs="?")
    p.set_defaults(run=stats)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
