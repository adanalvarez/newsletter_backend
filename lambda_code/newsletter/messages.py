"""The emails the sign-up function sends: the confirmation, and the welcome once someone confirms.

The confirmation never includes anything the visitor typed, so the form can't be used to send someone else a
message. Both look like an edition (tools/render.py): the system font, the site's colours and a dark mode.
"""

from html import escape
from string import Template

NEWSLETTER = "No Hype, No Fear"
POSTAL = "Barcelona, Spain"  # keep in step with NEWSLETTER_POSTAL in tools/cli.py
UNSUBSCRIBE = "{{amazonSESUnsubscribeUrl}}"
# The welcome is tagged like an edition, so its numbers show with `tools/cli.py stats welcome`.
WELCOME_TAG = "welcome"
LOGO = "https://adan.cloud/apple-touch-icon.png"
LINKEDIN = "https://www.linkedin.com/in/adanalvarezvilchez/"
TEXT, MUTED, LINK, LINE = "#023047", "#3d5566", "#0b6a87", "#d6e6ef"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# ---- the confirmation ----

SUBJECT = "Confirm your subscription to No Hype, No Fear"

CONFIRM_TEXT = [
    "Someone, hopefully you, asked to get No Hype, No Fear: my newsletter on how AI is changing security, about once a month.",
]
CONFIRM_AFTER = "If it wasn't you, ignore this email. You won't hear from me again."

# ---- the welcome ----
# Written in Adan's words. The intro follows Daniel Priestley's "Name, Same, Fame, Pain, Aim, Game": it's how
# readers will remember who he is.

WELCOME_SUBJECT = "Welcome to No Hype, No Fear"
WELCOME_PREHEADER = "Who I am, what to expect, and one question for you."
WELCOME = [
    # Hook
    "Thanks a lot for subscribing. In a time when information is abundant, it's an honor to have you here.",
    # Name, Same, Fame
    "I'm Adan Álvarez, a hands-on security architect and researcher. I've worked in different areas of security "
    "and specialized in AWS. My research has been featured in newsletters like tl;dr sec, CloudSecList and "
    "Detection Engineering Weekly.",
    # Pain
    "Like many, I'm working on implementing AI in security. I'm helping different companies and teams use it "
    "securely. And at the same time, I'm fighting attackers who already use it.",
    # Aim, Game
    "My current aim is making security AI-ready. My biggest goal is to help companies build trust and reduce risk, "
    "so they can scale securely.",
    # What arrives
    "This newsletter is one of the ways I believe I can help. About once a month, I share what I learn, what I test and "
    "the data that backs it. I try to avoid the hype, but also the fear.",
]
# One easy question: replies also teach Gmail and Outlook that the newsletter is wanted.
WELCOME_ASK = (
    "One thing I love is learning what others are doing in security. What's something exciting or interesting you're "
    "working on right now? Reply to this email or send me a DM on LinkedIn. One line is enough, and I read every reply."
)
# The first email from a new sender often lands in Promotions.
WELCOME_PS = "P.S. If this landed in Promotions or spam, please move it to your inbox so the next edition reaches you."
WELCOME_LINK = ("Until the next edition, you can find my research at", "/writing")

# ---- the layout ----

LAYOUT = Template(
    f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>$title</title>
<style>
  :root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
  @media (prefers-color-scheme: dark) {{
    .nl-page {{ background:#011c2b !important; }}
    .nl-body, .nl-strong {{ color:#e6f0f5 !important; }}
    .nl-muted {{ color:#9db8c7 !important; }}
    .nl-page a {{ color:#8ecae6 !important; }}
    .nl-page a.nl-button {{ background:#8ecae6 !important; color:#011c2b !important; }}
    .nl-rule {{ border-color:#12455f !important; }}
  }}
  [data-ogsc] .nl-body, [data-ogsc] .nl-strong {{ color:#e6f0f5 !important; }}
  [data-ogsc] .nl-muted {{ color:#9db8c7 !important; }}
  @media (max-width: 620px) {{ .nl-pad {{ padding-left:20px !important; padding-right:20px !important; }} }}
</style>
</head>
<body class="nl-page" style="margin:0;padding:0;background:#ffffff;-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">$preheader</div>
<div role="article" aria-roledescription="email" aria-label="$title" lang="en">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="nl-page" style="background:#ffffff;">
<tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;">

<tr><td class="nl-pad" style="padding:32px 24px 0;font-family:{FONT};">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td valign="middle" style="padding:0 12px 0 0;"><img src="{LOGO}" width="36" height="36" alt="" style="display:block;border:0;border-radius:9px;"></td>
<td valign="middle">
<div class="nl-strong" style="font-size:15px;line-height:1.3;font-weight:700;color:{TEXT};">{NEWSLETTER}</div>
<div class="nl-muted" style="font-size:13px;line-height:1.4;color:{MUTED};">$kicker</div>
</td>
</tr></table>
</td></tr>

<tr><td class="nl-pad" style="padding:22px 24px 0;"><div class="nl-rule" style="border-top:1px solid {LINE};font-size:1px;line-height:1px;">&nbsp;</div></td></tr>

<tr><td class="nl-pad nl-body" style="padding:30px 24px 0;font-family:{FONT};font-size:17px;line-height:1.65;color:{TEXT};">
$body
</td></tr>

<tr><td class="nl-pad" style="padding:20px 24px 0;"><div class="nl-rule" style="border-top:1px solid {LINE};font-size:1px;line-height:1px;">&nbsp;</div></td></tr>

<tr><td class="nl-pad nl-muted" style="padding:22px 24px 44px;font-family:{FONT};font-size:13px;line-height:1.65;color:{MUTED};">
$footer
</td></tr>

</table>
</td></tr>
</table>
</div>
</body>
</html>
"""
)


def _p(text, style="margin:0 0 18px;"):
    return f'<p style="{style}">{escape(text)}</p>'


def _link(url, label):
    return f'<a href="{escape(url, quote=True)}" style="color:{LINK};text-decoration:underline;">{escape(label)}</a>'


def _page(title, kicker, preheader, body, footer):
    return LAYOUT.substitute(title=escape(title), kicker=escape(kicker), preheader=escape(preheader), body=body, footer=footer)


def confirmation(link, site_url):
    site_label = site_url.split("//")[-1]
    button = (
        f'<p style="margin:8px 0 28px;"><a href="{escape(link, quote=True)}" class="nl-button" '
        f'style="display:inline-block;background:{TEXT};color:#ffffff;padding:13px 24px;border-radius:999px;'
        f'text-decoration:none;font-weight:700;font-size:16px;">Confirm my subscription</a></p>'
    )
    body = "\n".join([_p("Hi,"), *(_p(text) for text in CONFIRM_TEXT), button, _p(CONFIRM_AFTER)])
    footer = f'<p style="margin:0;">Adan &Aacute;lvarez &middot; {_link(site_url, site_label)}</p>'
    text = "\n\n".join(["Hi,", *CONFIRM_TEXT, f"Confirm your subscription here:\n{link}", CONFIRM_AFTER, f"Adan Álvarez\n{site_url}"]) + "\n"
    return {
        "subject": SUBJECT,
        "text": text,
        "html": _page(SUBJECT, "Confirm your subscription", "One click and you're in.", body, footer),
    }


def welcome(site_url, unsubscribe=UNSUBSCRIBE):
    lead, path = WELCOME_LINK
    research = f"{site_url}{path}"
    body = "\n".join(
        [
            _p("Hi,"),
            *(_p(text) for text in WELCOME),
            # "LinkedIn" in the ask links to the profile.
            _p(WELCOME_ASK).replace("LinkedIn", _link(LINKEDIN, "LinkedIn"), 1),
            f'<p style="margin:0 0 18px;">{escape(lead)} {_link(research, research.split("//")[-1])}.</p>',
            _p("Adan", "margin:0 0 18px;"),
            _p(WELCOME_PS, f"margin:0 0 18px;color:{MUTED};"),
        ]
    )
    footer = "\n".join(
        [
            f'<p style="margin:0 0 12px;">You\'re getting this because you subscribed to {NEWSLETTER} at adan.cloud. No tracking pixels, no click tracking.</p>',
            f'<p style="margin:0;">{_link(unsubscribe, "Unsubscribe")} &middot; {_link(f"{site_url}/privacy", "Privacy")}<br>Adan &Aacute;lvarez &middot; {POSTAL}</p>',
        ]
    )
    text = "\n\n".join(
        [
            "Hi,",
            *WELCOME,
            WELCOME_ASK.replace("LinkedIn", f"LinkedIn ({LINKEDIN})", 1),
            f"{lead} {research}",
            "Adan",
            WELCOME_PS,
            f"--\nYou're getting this because you subscribed to {NEWSLETTER} at adan.cloud.\n"
            f"Unsubscribe: {unsubscribe}\nPrivacy: {site_url}/privacy\nAdan Álvarez · {POSTAL}",
        ]
    ) + "\n"
    return {
        "subject": WELCOME_SUBJECT,
        "text": text,
        "html": _page(WELCOME_SUBJECT, "Welcome", WELCOME_PREHEADER, body, footer),
    }
