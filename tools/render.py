"""Turns an edition into the email: an HTML version (single column, inline styles, works in Outlook) and a
plain-text version. No tracking pixels and no rewritten links: the links in the email are the real ones.

The unsubscribe link is a placeholder: SES fills in {{amazonSESUnsubscribeUrl}} for each reader when the email
is sent through the contact list.
"""

import html
import re
from string import Template

import markdown

UNSUBSCRIBE = "{{amazonSESUnsubscribeUrl}}"
PREVIEW_UNSUBSCRIBE = "https://adan.cloud/newsletter#unsubscribe-link-goes-here"

NAVY, MUTED, LINK, AMBER, LINE, SOFT, PAGE = "#023047", "#3d5566", "#0b6a87", "#ffb703", "#d6e6ef", "#e1eff6", "#f3f8fb"
FONT = "Arial,Helvetica,sans-serif"

# Styles for the HTML that Markdown produces, written inline because many email apps drop <style> blocks.
STYLES = {
    "p": "margin:0 0 16px;",
    "h2": f"margin:30px 0 12px;font-family:{FONT};font-size:22px;line-height:1.25;color:{NAVY};",
    "h3": f"margin:24px 0 10px;font-family:{FONT};font-size:18px;line-height:1.3;color:{NAVY};",
    "ul": "margin:0 0 16px;padding-left:22px;",
    "ol": "margin:0 0 16px;padding-left:22px;",
    "li": "margin:0 0 6px;",
    "blockquote": f"margin:20px 0;padding:2px 0 2px 16px;border-left:3px solid {AMBER};color:{MUTED};",
    "img": "display:block;width:100%;max-width:536px;height:auto;margin:20px auto;border:0;border-radius:12px;",
    "a": f"color:{LINK};text-decoration:underline;",
    "hr": f"border:0;border-top:1px solid {LINE};margin:28px 0;",
    "table": "width:100%;border-collapse:collapse;margin:20px 0;font-size:14px;",
    "th": f"text-align:left;padding:8px;border-bottom:2px solid {LINE};",
    "td": f"padding:8px;border-bottom:1px solid {LINE};",
    "pre": f"background:{SOFT};padding:12px 14px;border-radius:8px;font-size:13px;white-space:pre-wrap;",
    "code": f"font-family:Menlo,Consolas,monospace;font-size:14px;background:{SOFT};padding:1px 4px;border-radius:4px;",
}
TAG_RE = re.compile(r"<(" + "|".join(sorted(STYLES, key=len, reverse=True)) + r")(?=[\s>/])([^>]*)>")

TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>$title</title>
</head>
<body style="margin:0;padding:0;background:$page;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">$preheader</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:$page;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:16px;">
<tr><td style="padding:30px 32px 4px;font-family:$font;">
<p style="margin:0;font-size:12px;letter-spacing:1px;text-transform:uppercase;font-weight:bold;color:$link;">$newsletter &middot; Edition $number</p>
<div style="height:3px;width:48px;background:$amber;margin:14px 0 18px;line-height:3px;font-size:1px;">&nbsp;</div>
<h1 style="margin:0 0 12px;font-family:$font;font-size:30px;line-height:1.15;color:$navy;">$title</h1>
<p style="margin:0 0 22px;font-size:17px;line-height:1.55;color:$muted;">$description</p>
</td></tr>
<tr><td style="padding:0 32px 6px;font-family:$font;font-size:16px;line-height:1.65;color:$navy;">
$body
</td></tr>
<tr><td style="padding:6px 32px 30px;font-family:$font;font-size:14px;color:$muted;">
<a href="$url" style="color:$link;font-weight:bold;">Read it on adan.cloud</a> &middot; <a href="$markdown_url" style="color:$link;">Markdown version</a>
</td></tr>
</table>
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;">
<tr><td style="padding:18px 32px;font-family:$font;font-size:12px;line-height:1.6;color:$muted;text-align:center;">
You're getting this because you subscribed to $newsletter at adan.cloud. No tracking pixels in this email.<br>
<a href="$unsubscribe" style="color:$link;">Unsubscribe</a> &middot; <a href="https://adan.cloud/privacy" style="color:$link;">Privacy</a><br>
Adan &Aacute;lvarez &middot; $postal
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
)


def _style(match):
    tag, attrs = match.group(1), match.group(2)
    extra = ' width="536"' if tag == "img" and "width=" not in attrs else ""  # Outlook ignores max-width
    return f'<{tag} style="{STYLES[tag]}"{extra}{attrs}>'


def body_html(markdown_text):
    rendered = markdown.markdown(markdown_text, extensions=["extra", "sane_lists"], output_format="html")
    return TAG_RE.sub(_style, rendered)


def to_html(edition, newsletter="No Hype, No Fear", postal="Barcelona, Spain", unsubscribe=UNSUBSCRIBE):
    return TEMPLATE.substitute(
        title=html.escape(edition.title),
        description=html.escape(edition.description),
        preheader=html.escape(edition.description),
        newsletter=html.escape(newsletter),
        number=edition.number,
        body=body_html(edition.body),
        url=html.escape(edition.url, quote=True),
        markdown_url=html.escape(edition.markdown_url, quote=True),
        unsubscribe=unsubscribe,
        postal=html.escape(postal),
        page=PAGE,
        font=FONT,
        link=LINK,
        amber=AMBER,
        navy=NAVY,
        muted=MUTED,
    )


def to_text(edition, newsletter="No Hype, No Fear", postal="Barcelona, Spain", unsubscribe=UNSUBSCRIBE):
    body = edition.body
    body = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"[Chart: \1]", body)  # images
    body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", body)  # links
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)  # bold
    return (
        f"{newsletter.upper()} · EDITION {edition.number}\n\n"
        f"{edition.title}\n\n{edition.description}\n\n"
        f"{body}\n\n"
        f"Read it on adan.cloud: {edition.url}\n\n"
        "--\n"
        f"You're getting this because you subscribed to {newsletter} at adan.cloud.\n"
        f"Unsubscribe: {unsubscribe}\n"
        f"Adan Álvarez · {postal}\n"
    )
