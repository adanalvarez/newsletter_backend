"""The confirmation email. It never includes anything the visitor typed, so the form can't be used to send
someone else a message."""

from html import escape

SUBJECT = "Confirm your subscription to No Hype, No Fear"

TEXT = """Hi,

Someone, hopefully you, asked to get No Hype, No Fear: my monthly newsletter on how AI is changing security.

Confirm your subscription here:
{link}

If it wasn't you, ignore this email. You won't hear from me again.

Adan Álvarez
{site}
"""

HTML = """<!doctype html>
<html lang="en">
  <body style="margin:0;padding:24px;background:#ffffff;color:#023047;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;">
    <p>Hi,</p>
    <p>Someone, hopefully you, asked to get <strong>No Hype, No Fear</strong>: my monthly newsletter on how AI is changing security.</p>
    <p style="margin:28px 0;">
      <a href="{link}" style="background:#023047;color:#ffffff;padding:12px 22px;border-radius:999px;text-decoration:none;font-weight:bold;">Confirm my subscription</a>
    </p>
    <p>If it wasn't you, ignore this email. You won't hear from me again.</p>
    <p>Adan Álvarez<br><a href="{site}" style="color:#0b6a87;">{site_label}</a></p>
  </body>
</html>
"""


def confirmation(link, site_url):
    return {
        "subject": SUBJECT,
        "text": TEXT.format(link=link, site=site_url),
        "html": HTML.format(link=escape(link, quote=True), site=escape(site_url, quote=True), site_label=escape(site_url.split("//")[-1])),
    }
