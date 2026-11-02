# newsletter

The sign-up backend for **No Hype, No Fear**, Adan Álvarez's newsletter on adan.cloud. Readers only ever see adan.cloud: the form posts to `adan.cloud/api/subscribe`, the confirmation email comes from adan.cloud, and its link goes to `adan.cloud/newsletter/confirm`.

This repo holds the machinery. The pages, the editions and the form live in the site repo (`adan.cloud`). Sending the monthly edition comes next; this first part collects and confirms readers.

## How it works

```
adan.cloud/newsletter (form) ──POST /api/subscribe──► Cloudflare ──► CloudFront ──► API Gateway ──► signup Lambda
                                                                                                  ├─ DynamoDB: pending sign-up
                                                                                                  └─ SES: confirmation email (link to /newsletter/confirm#t=…)
adan.cloud/newsletter/confirm (button) ──POST /api/confirm──► … ──► signup Lambda
                                                                    ├─ SES contact list: subscribed to the "newsletter" topic
                                                                    ├─ DynamoDB: confirmed, with the consent history
                                                                    └─ SES: welcome email (through the contact list)
SES (bounces, complaints, unsubscribes) ──► EventBridge ──► ses-events Lambda ──► DynamoDB + contact list
                                                     (failures ──► dead-letter queue + alarm)
```

### Security and privacy design
- **Double opt-in that link scanners can't trigger.** Nobody is added until they press the button on the confirmation page. The email link only opens that page, and its code is in the `#` fragment, so it never reaches a server, a CDN log or a Referer header. The page sends no referrer, and it removes the code from the address bar.
- **Only through the site, only from the site.**
  - CloudFront adds `X-Origin-Verify` and Cloudflare adds `X-Edge-Verify`. The function refuses requests missing either, so nobody can skip Cloudflare's rate limits by calling API Gateway or CloudFront directly.
  - POSTs must come from `https://adan.cloud` (the `Origin` header), so other sites can't submit the form for their visitors.
  - Both secrets accept a previous value during a rotation.
- **Limits on abuse of the form:**
  - **Honeypot:** a hidden field bots fill in.
  - **One confirmation email per inbox per day.** "Inbox" is canonical, so `+tags` and Gmail dots count as the same one.
  - **A daily ceiling** on confirmation emails (default 100). An alarm fires when it's reached.
  - **Cloudflare rate limiting** per visitor.
  - **API Gateway throttling** as an overall safety ceiling.
  - **Separate configuration set:** confirmation emails use their own, so they can be paused without stopping editions.
- **Same answer, same time.** New, pending or already subscribed: the form always lands on "check your inbox", and every answer is padded to about the same time. So nobody can use the form to find out who's subscribed.
- **Least privilege:**
  - the sign-up function can only send email **from the newsletter address** (`ses:FromAddress` condition), through the newsletter's two configuration sets;
  - it can't read the whole table (no Scan or Query);
  - each function has its own role.
- **Consent history** (GDPR Art. 7(1)): every confirmation and withdrawal is stored in the subscriber's record, with the date, the privacy-notice version the page showed, and the page the sign-up came from.
- **Retention:**
  - unconfirmed sign-ups expire with their link (48 hours);
  - after an unsubscribe, bounce or complaint, the record is kept for `retention_days` (3 years by default, pending the legal review), then deleted automatically;
  - addresses that never confirmed are kept only `unconfirmed_retention_days` (30).
- **Erasure:** `scripts/erase_reader.py` (below).
- **Logs** never contain an email address, only a 12-character HMAC with a secret key. They're kept 90 days.
- **Unsubscribe** is handled by SES list management (hosted page plus one-click `List-Unsubscribe` headers), and recorded by the events function.

### DynamoDB items
| Key | What |
|---|---|
| `SUB#<canonical email>` | address as typed, status (pending, confirmed, unsubscribed, bounced, complained), role, source page, consent version, dates, `history` |
| `TOKEN#<sha256>` | a pending confirmation link. Only the hash is stored; it's used once and expires after 48 hours |
| `COUNTER#<date>` | confirmation emails sent that day |
| `EDITION#<slug>` | an edition's numbers: sent, delivered, bounced, complained, unsubscribed (counts only) |
| `SENT#<slug>#<reader>` | who an edition went to, so a re-run never sends twice (expires after 180 days) |
| `POLL#<slug>#<question>` | anonymous answer counts (`a0`, `a1`, … and `total`) |
| `VOTER#<slug>#<question>#<code>` | a daily one-way code that stops repeat votes (deleted within 26 hours) |
| `COMMENT#<slug>#<question>#<time>#<random>` | an optional anonymous comment and its answer (deleted after a year) |

Writes that depend on the current state are conditional. Two requests at once can't both send an email, confirm twice, or overwrite a confirmed subscriber.

## Set it up

### 1. Before anything (in the consoles)
- [x] SES production access (eu-west-1), granted.
- [x] DMARC on adan.cloud (Cloudflare DMARC Management, `p=none`).
- [x] State bucket: `adan-terraform`, key `newsletter/terraform.tfstate` (`infra/backend.hcl`).

### 2. Deploy
```bash
cd infra
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```
Then confirm the SNS subscription email AWS sends to `alert_email`.

### 3. DNS in Cloudflare
```bash
terraform output dns_records
```
Add every record as **DNS only** (grey cloud): 3 DKIM CNAMEs, plus an MX and a TXT on `bounce.adan.cloud`. Don't change the root SPF record: Cloudflare Email Routing uses it, and SES aligns SPF through `bounce.adan.cloud`.

### 4–5. CloudFront and Cloudflare: the script
`scripts/edge_setup.py` does sections 4 and 5 below, and the CSP change from section 6, with the values read straight from `terraform output`. It shows the changes first, never prints a secret, and re-running it changes only what differs. That also makes it the way to roll out new secrets.

```bash
export CLOUDFLARE_API_TOKEN=...                 # zone adan.cloud: Zone Read, Transform Rules Edit, Zone WAF Edit, Cache Rules Edit
.venv/bin/python scripts/edge_setup.py          # dry run: what would change
.venv/bin/python scripts/edge_setup.py --apply  # change it, wait for CloudFront, test /api
.venv/bin/python scripts/edge_setup.py --check  # only the test
```

Sections 4 and 5 are the same steps by hand.

### 4. CloudFront (the site's distribution, by hand)
1. **Origins → Create origin**
   - Origin domain: `terraform output -raw cloudfront_origin_domain`
   - Protocol: HTTPS only
   - Custom header: `X-Origin-Verify` = `terraform output -raw origin_secret`
2. **Behaviors → Create behavior**
   - Path pattern: `/api/*`, origin: the one above
   - Viewer protocol: Redirect HTTP to HTTPS
   - Allowed methods: GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE
   - Cache policy: **CachingDisabled**
   - Origin request policy: **AllViewerExceptHostHeader**
   - Response headers policy: the same one as the default behavior
   - **No function association.** The site's viewer-request function would rewrite `/api/subscribe` to `/api/subscribe/index.html`.

### 5. Cloudflare (three rules, all on the free plan)
1. **Transform Rules → Modify request header:** for requests whose URI path starts with `/api/`, set the static header `X-Edge-Verify` to `terraform output -raw edge_secret`. Until this exists, `/api` refuses every request. That's on purpose: requests have to come through Cloudflare. (Visitors see the site's 404 page: the distribution's custom error pages replace the backend's 403.)
2. **Security → WAF → Rate limiting rules** (free plan: a 10-second period and a 10-second block): when the URI path starts with `/api/`, allow at most **3 requests per 10 seconds per IP**, then block.
3. **Caching → Cache Rules:** bypass the cache for URI paths starting with `/newsletter/`. CloudFront sends agents the Markdown version of the same URL, and Cloudflare ignores `Vary`, so it must never cache these pages.

### 6. Switch the site on (site repo)
- In `src/data/newsletter.js`, set:
  - `signupEndpoint: '/api/subscribe'`;
  - `privacyUrl: '/privacy'`;
  - `consentVersion`, which must be in `consent_versions`.
- Add the privacy page.
- CSP: change `form-action 'none'` to `form-action 'self'`.
- Deploy, then check the whole flow with `adan@adan.cloud`:
  1. sign up;
  2. the email arrives;
  3. the button confirms;
  4. the contact appears in the SES contact list, and the welcome email arrives;
  5. unsubscribe from a test edition later.

## Writing and sending an edition: the easy way

One command, `edition` (`tools/edition`, linked into `~/.local/bin`), runs the whole flow. It finds the site repo through `NEWSLETTER_SITE`.

```bash
edition new the-sixty-seconds tested how-to   # 1. start a draft, with the parts it mixes (opens in VS Code)
edition site the-sixty-seconds          # 2. see it on the site, locally (drafts show only here)
edition email the-sixty-seconds         # 3. see the email in your browser
edition test the-sixty-seconds          # 4. get it in your inbox: check it on your phone, in Gmail, in dark mode
                                        # 5. publish: remove `draft: true`, commit, push, deploy the site
edition test the-sixty-seconds --live   # 6. last check: the published version, with working images and poll buttons
edition send the-sixty-seconds          # 7. send it to everyone (you type the slug to confirm)
edition stats the-sixty-seconds         # 8. a few days later: the numbers, poll answers and comments
edition list                            # every edition, draft or published
```

While it's a draft, the email's images and poll buttons point to pages that aren't live yet. That's why step 6 exists.

## Sending an edition

One-time setup for the send tool:
```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
```

The flow, after the edition is written and published on adan.cloud (see the site repo's README, "Newsletter editions"):

```bash
.venv/bin/python tools/cli.py preview <slug>    # the email, opened in your browser (also works on a local .md draft)
.venv/bin/python tools/cli.py test <slug>       # sends it to adan@adan.cloud only, subject starts with [TEST]
.venv/bin/python tools/cli.py send <slug>       # shows the count, asks you to type the slug, then sends to every reader
.venv/bin/python tools/cli.py stats <slug>      # sent, delivered, bounced, complained, unsubscribed
.venv/bin/python tools/cli.py stats             # subscribers by status, and confirmed readers by role and by page
```

**How the send works:**
- It reads the published `adan.cloud/newsletter/<slug>.md`, so the email is exactly what's live. It refuses drafts.
- **One email per reader,** through the SES contact list. SES adds the one-click unsubscribe headers and fills in the unsubscribe link for each reader.
- **No double sends.** Each reader is claimed in DynamoDB (`SENT#<slug>#<reader>`) before their email goes out. Re-running the command (after a failure, or by accident) never emails anyone twice. Failed sends are released, so the next run retries them.
- **Throttled** to 5 emails a second (`--rate`).
- **Tagged** `edition=<slug>`, so SES events count towards that edition's numbers.

**What's tracked, and what isn't.** For each edition:
- **sent and delivered:** did it go out, and did the inbox servers accept it?
- **bounced and complained:** the health of the list and the domain;
- **unsubscribed.**

They're counts only, collected by the events function. **Opens and clicks aren't tracked:** the newsletter promises no tracking pixels, and open rates are unreliable anyway, since Apple Mail fakes most opens.

To see whether people read an edition, look at:
- **replies** to `adan@adan.cloud`;
- **visits to the edition's page:** CloudFront → Reports & analytics → Popular objects;
- **new sign-ups by page** (`stats`);
- **LinkedIn's numbers** for the LinkedIn edition.

## The welcome email

Everyone who confirms gets one welcome email, sent by the sign-up function right after they're subscribed.
- **The text** is in `lambda_code/newsletter/messages.py` (`WELCOME`), in Adan's words. The intro follows Daniel Priestley's "Name, Same, Fame, Pain, Aim, Game". To change it, edit the text, preview it, then `terraform apply` to deploy the function.
- **It goes through the contact list,** like an edition: it has a real unsubscribe link and one-click unsubscribe.
- **Its numbers:** it's tagged `edition=welcome`, so `stats welcome` shows how many were sent, delivered, bounced or marked as spam, and who unsubscribed from it. That's why `welcome` can't be an edition's slug.
- **If it fails,** the reader stays subscribed. The log records `WELCOME_FAILED` with the SES error code (never the address), and an alarm emails you.

```bash
.venv/bin/python tools/cli.py welcome          # preview it in your browser
.venv/bin/python tools/cli.py welcome --test   # send it to adan@adan.cloud
```

## Polls and "Was this edition useful?"

Readers can answer a poll in an edition and say whether each edition was useful, **anonymously**.

**Writing a poll** (in the edition's Markdown, site repo):
````
```poll alert-speed
How long until a human sees your first alert?
- Under 5 minutes
- 5 to 60 minutes
- More than an hour
```
````

**Where it shows up:**
- **On the site:** a form.
- **In the email:** a card of answer buttons.
- **In the feed:** a link to answer on the site.
- **Every edition** also ends with "Was this edition useful?" (Yes / Somewhat / No).

**How an answer travels:**
1. An email button opens `adan.cloud/newsletter/vote#e=<edition>&q=<question>&a=<answer>`. Everything after `#` stays in the browser.
2. The page shows the question with that answer selected, and an optional comment box.
3. The reader sends it: a POST to `/api/vote`, which runs on the **feedback** function.
4. Email link scanners open links but don't press buttons, so they can't vote.

**Why it's anonymous:**
- No reader ID in any link.
- No email.
- The IP address is never stored. Repeat votes are stopped with a one-way code made from the IP address, the question and the day, using a secret key. The code changes daily and is deleted within 26 hours.

**Why it's safe:**
- **Origin checks:** the same edge and origin secrets and same-site check as sign-up.
- **Only real questions count:** answers are checked against `newsletter/polls.json`, which the site builds from published editions and the function reads from the site's S3 bucket.
- **Comments** are capped at 1,000 characters. Control and direction-override characters are stripped when they come in, and again when `stats` prints them.
- **Its own minimal role:** the function can only write `POLL#`, `VOTER#` and `COMMENT#` items (an IAM `dynamodb:LeadingKeys` condition), and read that one S3 object. No SES, and no subscribers.
- **Caveat:** the daily code relies on Cloudflare's `CF-Connecting-IP`, which can be trusted only because the edge secret proves the request came through Cloudflare. Keep `require_edge_secret` on.

**Results:** `.venv/bin/python tools/cli.py stats <slug>` shows the counts and percentages for each question, plus the comments.

**Switching it on:** after `terraform apply` creates the feedback function, set `feedbackEndpoint: '/api/vote'` in the site's `src/data/newsletter.js`. Until then, polls show on the site as a plain list and the "useful" question is hidden. The `/api/*` CloudFront behavior already covers `/api/vote`.

## Running it

**A spike of sign-ups (announcement day).** Raise `daily_confirmation_limit` for the day and `terraform apply`. The "daily ceiling reached" alarm tells you when real readers are being turned away.

**A flood of fake sign-ups:**
1. Pause confirmation emails without touching editions:
   ```bash
   aws sesv2 put-configuration-set-sending-options --configuration-set-name newsletter-confirmations --no-sending-enabled
   ```
2. Look at the Cloudflare firewall events, and tighten the rate limit or add a Cloudflare challenge on `/newsletter`.
3. Re-enable confirmations with `--sending-enabled`.

**The dead-letter queue alarm.** An SES event (bounce, complaint, unsubscribe) failed to process. Fix the cause, then replay each message in `newsletter-ses-events-dlq` to the `newsletter-ses-events` function.

**Rotating a secret:**
1. Put the current value in `previous_origin_secrets`, `taint` the `random_password`, and apply.
2. Update the CloudFront header or the Cloudflare rule.
3. Empty `previous_origin_secrets`, and apply again.

**An erasure request (GDPR Art. 17):**
```bash
python3 scripts/erase_reader.py reader@example.com        # dry run
python3 scripts/erase_reader.py reader@example.com --yes
```
It deletes the record and its consent history, the SES contact, and the suppression-list entry. Tell the reader that table backups age out within 35 days, and that logs never held their address.

## Tests
```bash
python3 -m unittest discover -s tests          # the Lambda handlers: no packages needed
.venv/bin/python -m unittest discover -s tests  # also the send tool (needs tools/requirements.txt)
```
The tests use in-memory fakes for DynamoDB and SES, so no AWS account is needed.

## Costs
Pennies at this size: SES at $0.10–0.16 per 1,000 emails, DynamoDB on-demand, Lambda, API Gateway, SQS and alarms within or near the free tier.
