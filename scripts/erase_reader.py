"""Erases one reader everywhere the newsletter keeps them, for a GDPR erasure request.

    python3 scripts/erase_reader.py reader@example.com          # shows what would be deleted
    python3 scripts/erase_reader.py reader@example.com --yes    # deletes it

Run with credentials for the newsletter's AWS account, in its region. It deletes:
- the subscriber record (SUB#<canonical address>), which also holds their consent history;
- their contact in the SES contact list;
- their entry in the SES account suppression list.

What it can't reach, and why that's fine: an unused confirmation link (TOKEN#) expires within 48 hours,
point-in-time backups of the table age out after 35 days, and the Lambda logs only hold a keyed hash, never
the address. Mention these in the reply to the reader.
"""

import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda_code"))
from newsletter.validation import canonical, normalize_email  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--table", default="newsletter")
    parser.add_argument("--contact-list", default="newsletter")
    parser.add_argument("--yes", action="store_true", help="actually delete")
    args = parser.parse_args()

    email = normalize_email(args.email)
    if not email:
        sys.exit("That doesn't look like an email address.")
    key = canonical(email)
    table = boto3.resource("dynamodb").Table(args.table)
    ses = boto3.client("sesv2")

    item = table.get_item(Key={"pk": f"SUB#{key}"}).get("Item")
    print(f"Subscriber record SUB#{key}: {'found, status ' + item.get('status', '?') if item else 'not found'}")
    if not args.yes:
        print("Dry run. Add --yes to delete the record, the SES contact and the suppression-list entry.")
        return

    table.delete_item(Key={"pk": f"SUB#{key}"})
    for address in {email, item.get("email") if item else None} - {None}:
        try:
            ses.delete_contact(ContactListName=args.contact_list, EmailAddress=address)
            print(f"Deleted SES contact {address}")
        except ses.exceptions.NotFoundException:
            print(f"No SES contact for {address}")
        try:
            ses.delete_suppressed_destination(EmailAddress=address)
            print(f"Removed {address} from the suppression list")
        except ses.exceptions.NotFoundException:
            print(f"{address} wasn't on the suppression list")
    print("Done. Backups age out within 35 days; logs never held the address.")


if __name__ == "__main__":
    main()
