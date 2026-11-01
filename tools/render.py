"""Turns an edition into the email: an HTML version and a plain-text version.

Design: an editorial letter, not a marketing template. White page, one left-aligned column, the system font of
each device, the site's colours, a dark mode, and two signature pieces: the "If you decide / If you build"
callouts and amber-edged pull quotes. No tracking pixels and no rewritten links.

Email clients support very little CSS (checked on caniemail.com, Sep 2026). So:
- styles are inline (Gmail drops most <style> rules);
- dark mode is in a <style> block (prefers-color-scheme: Apple Mail, Outlook apps; Gmail inverts by itself);
- no web fonts (Gmail and most Outlook ignore @font-face);
- no SVG (Gmail ignores it);
- images get a width attribute (classic Outlook ignores max-width).

The unsubscribe link is a placeholder: SES fills in {{amazonSESUnsubscribeUrl}} for each reader when the email
is sent through the contact list.
"""

import html
import math
import re
from datetime import date as Date
from string import Template

import markdown

UNSUBSCRIBE = "{{amazonSESUnsubscribeUrl}}"
PREVIEW_UNSUBSCRIBE = "https://adan.cloud/newsletter#unsubscribe-link-goes-here"
LOGO = "https://adan.cloud/apple-touch-icon.png"
# Gmail cuts off ("[Message clipped]") emails whose HTML is over about 102 KB.
GMAIL_CLIP_BYTES = 102_000

# The site's tokens (adan.cloud/src/styles/global.css).
TEXT, MUTED, LINK, AMBER, PRIMARY, LINE, SURFACE, SOFT = "#023047", "#3d5566", "#0b6a87", "#ffb703", "#8ecae6", "#d6e6ef", "#f3f8fb", "#e1eff6"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
WIDTH = 592  # content width inside the 640px column

# Styles for the HTML that Markdown produces, written inline because Gmail drops most <style> rules.
STYLES = {
    "p": "margin:0 0 18px;",
    "h2": f"margin:36px 0 10px;font-size:21px;line-height:1.3;font-weight:700;letter-spacing:-0.2px;color:{TEXT};",
    "h3": f"margin:28px 0 8px;font-size:18px;line-height:1.35;font-weight:700;color:{TEXT};",
    "ul": "margin:0 0 18px;padding-left:22px;",
    "ol": "margin:0 0 18px;padding-left:22px;",
    "li": "margin:0 0 8px;",
    "blockquote": f"margin:30px 0;padding:2px 0 2px 18px;border-left:3px solid {AMBER};font-size:20px;line-height:1.45;font-weight:600;color:{TEXT};",
    "img": f"display:block;width:100%;max-width:{WIDTH}px;height:auto;border:0;border-radius:10px;",
    "a": f"color:{LINK};text-decoration:underline;",
    "hr": f"border:0;border-top:1px solid {LINE};margin:36px 0;",
    "table": "width:100%;border-collapse:collapse;margin:24px 0;font-size:15px;",
    "th": f"text-align:left;padding:8px 10px 8px 0;border-bottom:2px solid {LINE};",
    "td": f"padding:8px 10px 8px 0;border-bottom:1px solid {LINE};",
    "pre": f"margin:0 0 18px;padding:14px 16px;background:{SURFACE};border-radius:8px;font-family:{MONO};font-size:13px;line-height:1.5;white-space:pre-wrap;",
    "code": f"font-family:{MONO};font-size:15px;background:{SOFT};padding:1px 5px;border-radius:4px;",
}
# Polls in an edition are written as a fenced block (the site turns the same block into a form):
#   ```poll alert-speed
#   How long until a human sees your first alert?
#   - Under 5 minutes
#   - More than an hour
#   ```
POLL_RE = re.compile(r"^```poll[ \t]+([a-z0-9]+(?:-[a-z0-9]+)*)[ \t]*\n(.*?)\n```[ \t]*$", re.M | re.S)
USEFUL_QUESTION, USEFUL_OPTIONS = "Was this edition useful?", ["Yes", "Somewhat", "No"]


def parse_poll(block):
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    options = [line[2:].strip() for line in lines[1:] if line.startswith("- ")]
    if not lines or not 2 <= len(options) <= 10:
        raise ValueError(f"A poll needs a question and 2 to 10 options starting with '- ': {block[:60]!r}")
    return lines[0], options


def vote_url(edition, question, answer):
    # Everything after "#" stays in the browser: the vote page reads it, and no server or log ever sees it.
    site = edition.url.split("/newsletter/")[0]
    return f"{site}/newsletter/vote#e={edition.slug}&q={question}&a={answer}"


TAG_RE = re.compile(r"<(" + "|".join(sorted(STYLES, key=len, reverse=True)) + r")(?=[\s>/])([^>]*)>")
CALLOUT_RE = re.compile(r"<h2[^>]*>(If you (?:decide|build))</h2>\s*(.*?)(?=<h2|<hr|\Z)", re.S | re.I)

CALLOUT = Template(
    f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="nl-callout" style="margin:22px 0 0;background:{SURFACE};border-left:3px solid {PRIMARY};border-radius:0 10px 10px 0;">
<tr><td style="padding:18px 20px 2px;">
<div class="nl-strong" style="margin:0 0 6px;font-size:17px;font-weight:700;color:{TEXT};">$label</div>
$content
</td></tr>
</table>
"""
)

POLL_OPTION = Template(
    f"""<a href="$url" class="nl-option" style="display:block;margin:0 0 8px;padding:11px 14px;border:1.5px solid {LINE};border-radius:8px;background:#ffffff;color:{TEXT};text-decoration:none;font-size:16px;">$label</a>"""
)
POLL = Template(
    f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="nl-callout" style="margin:26px 0;background:{SURFACE};border-radius:10px;">
<tr><td style="padding:18px 20px 14px;">
<div class="nl-strong" style="margin:0 0 12px;font-size:17px;font-weight:700;color:{TEXT};">$question</div>
$options
<div class="nl-muted" style="margin:6px 0 0;font-size:13px;color:{MUTED};">Anonymous: one click opens a page where you confirm, and can add a comment.</div>
</td></tr>
</table>"""
)
USEFUL = Template(
    f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:34px 0 0;">
<tr><td class="nl-strong" style="padding:0 14px 0 0;font-size:15px;font-weight:700;color:{TEXT};">$question</td>
$cells
</tr>
</table>"""
)
USEFUL_CELL = Template(
    f"""<td style="padding:4px 6px 4px 0;"><a href="$url" class="nl-option" style="display:inline-block;padding:7px 14px;border:1.5px solid {LINE};border-radius:999px;background:#ffffff;color:{TEXT};text-decoration:none;font-size:14px;">$label</a></td>"""
)

TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>$title</title>
<style>
  :root { color-scheme: light dark; supported-color-schemes: light dark; }
  @media (prefers-color-scheme: dark) {
    .nl-page { background:#011c2b !important; }
    .nl-body, .nl-body h2, .nl-body h3, .nl-body blockquote, .nl-strong, .nl-title { color:#e6f0f5 !important; }
    .nl-muted { color:#9db8c7 !important; }
    .nl-page a { color:#8ecae6 !important; }
    .nl-callout, .nl-body pre, .nl-body code { background:#03293d !important; }
    .nl-rule, .nl-body hr, .nl-body th, .nl-body td { border-color:#12455f !important; }
    .nl-page a.nl-option { background:#011c2b !important; border-color:#12455f !important; color:#e6f0f5 !important; }
  }
  [data-ogsc] .nl-body, [data-ogsc] .nl-strong, [data-ogsc] .nl-title { color:#e6f0f5 !important; }
  [data-ogsc] .nl-muted { color:#9db8c7 !important; }
  [data-ogsb] .nl-callout { background:#03293d !important; }
  @media (max-width: 620px) {
    .nl-pad { padding-left:20px !important; padding-right:20px !important; }
    .nl-title { font-size:28px !important; }
  }
</style>
</head>
<body class="nl-page" style="margin:0;padding:0;background:#ffffff;-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">$preheader$spacer</div>
<div role="article" aria-roledescription="email" aria-label="$title" lang="en">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="nl-page" style="background:#ffffff;">
<tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;">

<tr><td class="nl-pad" style="padding:32px 24px 0;font-family:$font;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td valign="middle" style="padding:0 12px 0 0;"><img src="$logo" width="36" height="36" alt="" style="display:block;border:0;border-radius:9px;"></td>
<td valign="middle">
<div class="nl-strong" style="font-size:15px;line-height:1.3;font-weight:700;color:$text;">$newsletter</div>
<div class="nl-muted" style="font-size:13px;line-height:1.4;color:$muted;">Edition $number &middot; $date &middot; $minutes min read &middot; <a href="$url" style="color:$link;">Read online</a></div>
</td>
</tr></table>
</td></tr>

<tr><td class="nl-pad" style="padding:22px 24px 0;"><div class="nl-rule" style="border-top:1px solid $line;font-size:1px;line-height:1px;">&nbsp;</div></td></tr>

<tr><td class="nl-pad" style="padding:30px 24px 0;font-family:$font;">
<h1 class="nl-title" style="margin:0 0 14px;font-size:34px;line-height:1.15;font-weight:800;letter-spacing:-0.6px;color:$text;">$title</h1>
<p class="nl-muted" style="margin:0 0 30px;font-size:19px;line-height:1.5;color:$muted;">$description</p>
</td></tr>

<tr><td class="nl-pad nl-body" style="padding:0 24px;font-family:$font;font-size:17px;line-height:1.65;color:$text;">
$body
</td></tr>

<tr><td class="nl-pad" style="padding:36px 24px 0;"><div class="nl-rule" style="border-top:1px solid $line;font-size:1px;line-height:1px;">&nbsp;</div></td></tr>

<tr><td class="nl-pad nl-muted" style="padding:22px 24px 44px;font-family:$font;font-size:13px;line-height:1.65;color:$muted;">
<p style="margin:0 0 12px;"><span class="nl-strong" style="font-weight:700;color:$text;">Reply to this email.</span> I read every reply, and they shape what I write next.</p>
<p style="margin:0 0 12px;">You're getting $newsletter because you subscribed at adan.cloud. No tracking pixels, no click tracking.</p>
<p style="margin:0;"><a href="$unsubscribe" style="color:$link;">Unsubscribe</a> &middot; <a href="$url" style="color:$link;">Read online</a> &middot; <a href="$markdown_url" style="color:$link;">Markdown, for your AI tools</a> &middot; <a href="https://adan.cloud/privacy" style="color:$link;">Privacy</a><br>Adan &Aacute;lvarez &middot; $postal</p>
</td></tr>

</table>
</td></tr>
</table>
</div>
</body>
</html>
"""
)


def _style(match):
    tag, attrs = match.group(1), match.group(2)
    extra = f' width="{WIDTH}"' if tag == "img" and "width=" not in attrs else ""  # classic Outlook ignores max-width
    return f'<{tag} style="{STYLES[tag]}"{extra}{attrs}>'


def _callout(match):
    return CALLOUT.substitute(label=match.group(1), content=match.group(2).strip())


def poll_html(edition, question_id, question, options):
    rows = "\n".join(
        POLL_OPTION.substitute(url=html.escape(vote_url(edition, question_id, i), quote=True), label=html.escape(label))
        for i, label in enumerate(options)
    )
    return POLL.substitute(question=html.escape(question), options=rows)


def useful_html(edition):
    cells = "\n".join(
        USEFUL_CELL.substitute(url=html.escape(vote_url(edition, "useful", i), quote=True), label=html.escape(label))
        for i, label in enumerate(USEFUL_OPTIONS)
    )
    return USEFUL.substitute(question=USEFUL_QUESTION, cells=cells)


def body_html(edition):
    polls = []

    def placeholder(match):
        polls.append(poll_html(edition, match.group(1), *parse_poll(match.group(2))))
        return f"\n\nNLPOLL{len(polls) - 1}\n\n"

    text = POLL_RE.sub(placeholder, edition.body)
    rendered = markdown.markdown(text, extensions=["extra", "sane_lists"], output_format="html")
    styled = TAG_RE.sub(_style, rendered)
    # "If you decide" and "If you build" become the newsletter's signature callouts.
    styled = CALLOUT_RE.sub(_callout, styled)
    for i, block in enumerate(polls):
        styled = re.sub(rf"<p[^>]*>NLPOLL{i}</p>", lambda _: block, styled)
    return styled + "\n" + useful_html(edition)


def reading_minutes(text):
    return max(1, math.ceil(len(re.findall(r"\w+", text)) / 230))


def display_date(iso):
    try:
        day = Date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{day:%b} {day.day}, {day.year}"  # the site's format: Nov 15, 2026


def to_html(edition, newsletter="No Hype, No Fear", postal="Barcelona, Spain", unsubscribe=UNSUBSCRIBE):
    return TEMPLATE.substitute(
        title=html.escape(edition.title),
        description=html.escape(edition.description),
        preheader=html.escape(edition.description),
        # Invisible filler, so the inbox preview stops after the description instead of pulling in body text.
        spacer="&#8199;&#65279;&#847; " * 60,
        newsletter=html.escape(newsletter),
        number=edition.number,
        date=html.escape(display_date(edition.date)),
        minutes=reading_minutes(edition.body),
        body=body_html(edition),
        url=html.escape(edition.url, quote=True),
        markdown_url=html.escape(edition.markdown_url, quote=True),
        unsubscribe=unsubscribe,
        postal=html.escape(postal),
        logo=LOGO,
        font=FONT,
        text=TEXT,
        muted=MUTED,
        link=LINK,
        line=LINE,
    )


def to_text(edition, newsletter="No Hype, No Fear", postal="Barcelona, Spain", unsubscribe=UNSUBSCRIBE):
    def poll_text(match):
        question, options = parse_poll(match.group(2))
        lines = "\n".join(f"  - {label}: {vote_url(edition, match.group(1), i)}" for i, label in enumerate(options))
        return f"POLL: {question}\n{lines}"

    body = POLL_RE.sub(poll_text, edition.body)
    body = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"[Chart: \1]", body)  # images
    body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", body)  # links
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)  # bold
    return (
        f"{newsletter} · Edition {edition.number} · {display_date(edition.date)}\n"
        f"Read online: {edition.url}\n\n"
        f"{edition.title.upper()}\n\n{edition.description}\n\n"
        f"{body}\n\n"
        f"{USEFUL_QUESTION} "
        + " · ".join(f"{label}: {vote_url(edition, 'useful', i)}" for i, label in enumerate(USEFUL_OPTIONS))
        + "\n\n--\n"
        "Reply to this email: I read every reply.\n"
        f"You're getting {newsletter} because you subscribed at adan.cloud. No tracking.\n"
        f"Unsubscribe: {unsubscribe}\n"
        f"Adan Álvarez · {postal}\n"
    )
