"""Loads an edition: from the live site (what's published) or from a local Markdown file (to preview a draft).

The live version is adan.cloud/newsletter/<slug>.md, which the site builds with front matter (title, subject,
description, edition, date, url) followed by the body. A local file is the source in the site repo
(src/content/newsletter/<slug>.md), with the same front matter keys.
"""

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SITE = "https://adan.cloud"
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass
class Edition:
    slug: str
    title: str
    subject: str
    description: str
    number: int
    date: str
    url: str
    body: str  # Markdown, without the title
    draft: bool = False

    @property
    def markdown_url(self):
        return f"{self.url}.md"


def _value(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        return json.loads(raw[: raw.rindex('"') + 1])
    return raw.split(" #", 1)[0].strip()


def parse(text, slug, site=SITE):
    if not text.startswith("---"):
        raise ValueError("The edition has no front matter (it should start with ---).")
    _, front, body = text.split("---", 2)
    meta = {}
    for line in front.strip().splitlines():
        key, _, raw = line.partition(":")
        if key.strip():
            meta[key.strip()] = _value(raw)
    body = body.strip()
    if body.startswith("# "):  # the published version repeats the title as a heading
        body = body.split("\n", 1)[1].strip() if "\n" in body else ""
    # Links and images written as /path become absolute, so they work in an inbox.
    body = re.sub(r"\]\(/", f"]({site}/", body)
    missing = [key for key in ("title", "subject", "description", "edition", "date") if not meta.get(key)]
    if missing:
        raise ValueError(f"The edition's front matter is missing: {', '.join(missing)}")
    return Edition(
        slug=slug,
        title=meta["title"],
        subject=meta["subject"],
        description=meta["description"],
        number=int(meta["edition"]),
        date=meta["date"],
        url=meta.get("url") or f"{site}/newsletter/{slug}",
        body=body,
        draft=str(meta.get("draft", "false")).lower() == "true",
    )


def load(source, site=SITE):
    """`source` is a slug (fetched from the live site) or a path to a local .md file."""
    path = Path(source)
    if path.suffix == ".md" and path.exists():
        return parse(path.read_text(encoding="utf-8"), path.stem, site)
    if not SLUG_RE.fullmatch(source):
        raise ValueError(f"'{source}' is neither a local .md file nor an edition slug.")
    return fetch(source, site)


def fetch(slug, site=SITE):
    request = urllib.request.Request(f"{site}/newsletter/{slug}.md", headers={"User-Agent": "newsletter-send-tool"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return parse(response.read().decode("utf-8"), slug, site)
