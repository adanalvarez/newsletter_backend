"""Connects adan.cloud's edge (CloudFront and Cloudflare) to the newsletter backend, from the Terraform outputs.

    .venv/bin/python scripts/edge_setup.py            # shows what it would change (dry run)
    .venv/bin/python scripts/edge_setup.py --apply    # changes it, waits for CloudFront, then tests /api
    .venv/bin/python scripts/edge_setup.py --check    # only tests /api through the edge

What it sets up (README, "Set it up", sections 4 and 5):
- CloudFront, on the site's distribution:
  - the `newsletter-api` origin (API Gateway), with the X-Origin-Verify header;
  - the /api/* behavior: all methods, CachingDisabled, AllViewerExceptHostHeader, the default behavior's response
    headers policy, and no function (the site's function would rewrite /api/subscribe to /api/subscribe/index.html);
  - the CSP: form-action 'none' becomes 'self', in the response headers policy or in Cloudflare, wherever it is.
- Cloudflare, on the adan.cloud zone:
  - a request header rule that adds X-Edge-Verify to /api/ requests;
  - a rate limit on /api/: 3 requests per 10 seconds per IP;
  - a cache rule that never caches /newsletter/ pages (CloudFront serves Markdown on the same URLs to agents).

Values come straight from `terraform output`, so nothing is copied by hand, and secrets are never printed.
Running it again is safe: it changes only what differs, which also makes it the way to roll out new secrets.

Needs:
- AWS credentials that can read and update the distribution and its response headers policy;
- CLOUDFLARE_API_TOKEN: a token for the adan.cloud zone with Zone Read, Transform Rules Edit, Zone WAF Edit and
  Cache Rules Edit (Cloudflare → My Profile → API Tokens). Delete it when you're done. Without it, only the
  CloudFront part runs.
"""

import argparse
import copy
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTION = os.environ.get("NEWSLETTER_DISTRIBUTION", "E32IFSKJTQSQKB")
ZONE = os.environ.get("NEWSLETTER_ZONE", "adan.cloud")
SITE = f"https://{ZONE}"
ORIGIN_ID = "newsletter-api"
API_PATH = "/api/*"
METHODS = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
NO_FORMS, SAME_SITE_FORMS = "form-action 'none'", "form-action 'self'"
CSP_HEADER = "content-security-policy"

API_MATCH = 'starts_with(http.request.uri.path, "/api/")'
# Some plans don't allow functions in rate limiting rules; this is the plain version Cloudflare then gets.
RATE_LIMIT_FALLBACK = 'http.request.uri.path contains "/api/"'


def hidden(secret):
    return f"hidden, {len(secret)} characters"


def same(existing, wanted):
    """True if `existing` has every value in `wanted`; other keys are ignored and lists compare in any order."""
    if isinstance(wanted, dict):
        return isinstance(existing, dict) and all(same(existing.get(key), value) for key, value in wanted.items())
    if isinstance(wanted, list):
        def key(item):
            return json.dumps(item, sort_keys=True, default=str)

        return isinstance(existing, list) and sorted(map(key, existing)) == sorted(map(key, wanted))
    return existing == wanted


# ---- CloudFront ----


def desired_origin(domain, secret):
    return {
        "Id": ORIGIN_ID,
        "DomainName": domain,
        "OriginPath": "",
        "CustomHeaders": {"Quantity": 1, "Items": [{"HeaderName": "X-Origin-Verify", "HeaderValue": secret}]},
        "CustomOriginConfig": {
            "HTTPPort": 80,
            "HTTPSPort": 443,
            "OriginProtocolPolicy": "https-only",
            "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
            "OriginReadTimeout": 30,
            "OriginKeepaliveTimeout": 5,
        },
        "ConnectionAttempts": 3,
        "ConnectionTimeout": 10,
        "OriginShield": {"Enabled": False},
        "OriginAccessControlId": "",
    }


def desired_behavior(cache_policy, request_policy, headers_policy):
    behavior = {
        "PathPattern": API_PATH,
        "TargetOriginId": ORIGIN_ID,
        "TrustedSigners": {"Enabled": False, "Quantity": 0},
        "TrustedKeyGroups": {"Enabled": False, "Quantity": 0},
        "ViewerProtocolPolicy": "redirect-to-https",
        "AllowedMethods": {"Quantity": len(METHODS), "Items": METHODS, "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
        "SmoothStreaming": False,
        "Compress": True,
        "LambdaFunctionAssociations": {"Quantity": 0},
        "FunctionAssociations": {"Quantity": 0},
        "FieldLevelEncryptionId": "",
        "CachePolicyId": cache_policy,
        "OriginRequestPolicyId": request_policy,
    }
    if headers_policy:
        behavior["ResponseHeadersPolicyId"] = headers_policy
    return behavior


def plan_distribution(config, domain, secret, cache_policy, request_policy):
    """The distribution config with the API origin and behavior in place, and what changed."""
    config = copy.deepcopy(config)
    changes = []

    origins = config["Origins"].setdefault("Items", [])
    wanted = desired_origin(domain, secret)
    origin = next((item for item in origins if item["Id"] == ORIGIN_ID), None)
    if origin is None:
        origins.append(wanted)
        changes.append(f"+ origin {ORIGIN_ID} → {domain}, X-Origin-Verify ({hidden(secret)})")
    elif not same(origin, wanted):
        origin.update(wanted)
        changes.append(f"~ origin {ORIGIN_ID}: domain, header or settings brought in line → {domain}")
    config["Origins"]["Quantity"] = len(origins)

    headers_policy = config["DefaultCacheBehavior"].get("ResponseHeadersPolicyId", "")
    behaviors = config.setdefault("CacheBehaviors", {"Quantity": 0}).setdefault("Items", [])
    wanted = desired_behavior(cache_policy, request_policy, headers_policy)
    behavior = next((item for item in behaviors if item["PathPattern"] == API_PATH), None)
    if behavior is None:
        behaviors.insert(0, wanted)  # first, so it takes precedence over any other pattern
        changes.append(f"+ behavior {API_PATH} → {ORIGIN_ID}: all methods, CachingDisabled, AllViewerExceptHostHeader, no function")
    elif not same(behavior, wanted):
        behavior.update(wanted)
        changes.append(f"~ behavior {API_PATH}: settings brought in line (all methods, no cache, no function)")
    config["CacheBehaviors"]["Quantity"] = len(behaviors)
    return config, changes


def plan_headers_policy(policy):
    """For a response headers policy: (the policy with form-action 'self', or None if no change) and its CSP state."""
    policy = copy.deepcopy(policy)
    security = (policy.get("SecurityHeadersConfig") or {}).get("ContentSecurityPolicy")
    if security:
        if NO_FORMS in security["ContentSecurityPolicy"]:
            security["ContentSecurityPolicy"] = security["ContentSecurityPolicy"].replace(NO_FORMS, SAME_SITE_FORMS)
            return policy, "change"
        return None, "done" if SAME_SITE_FORMS in security["ContentSecurityPolicy"] else "other"
    for header in (policy.get("CustomHeadersConfig") or {}).get("Items", []):
        if header["Header"].lower() == CSP_HEADER:
            if NO_FORMS in header["Value"]:
                header["Value"] = header["Value"].replace(NO_FORMS, SAME_SITE_FORMS)
                return policy, "change"
            return None, "done" if SAME_SITE_FORMS in header["Value"] else "other"
    return None, "missing"


# ---- Cloudflare ----


def cloudflare_rules(edge_secret):
    """(phase, rule) for each rule this script owns. Rules are found again by their description."""
    return [
        (
            "http_request_late_transform",
            {
                "description": "newsletter api edge secret",
                "expression": API_MATCH,
                "action": "rewrite",
                "action_parameters": {"headers": {"X-Edge-Verify": {"operation": "set", "value": edge_secret}}},
                "enabled": True,
            },
        ),
        (
            "http_ratelimit",
            {
                "description": "newsletter api rate limit",
                "expression": API_MATCH,
                "action": "block",
                "ratelimit": {"characteristics": ["ip.src", "cf.colo.id"], "period": 10, "requests_per_period": 3, "mitigation_timeout": 10},
                "enabled": True,
            },
        ),
        (
            "http_request_cache_settings",
            {
                "description": "newsletter no cache",
                "expression": 'starts_with(http.request.uri.path, "/newsletter/")',
                "action": "set_cache_settings",
                "action_parameters": {"cache": False},
                "enabled": True,
            },
        ),
    ]


def plan_rule(ruleset, wanted):
    """('add' | 'update' | None, the existing rule's id)."""
    existing = next((rule for rule in (ruleset or {}).get("rules", []) if rule.get("description") == wanted["description"]), None)
    if existing is None:
        return "add", None
    if same(existing, wanted) or (wanted.get("ratelimit") and same(existing, {**wanted, "expression": RATE_LIMIT_FALLBACK})):
        return None, existing["id"]
    return "update", existing["id"]


def plan_cloudflare_csp(rule):
    """A response header rule with form-action 'self', or None if it has no CSP to change."""
    headers = (rule.get("action_parameters") or {}).get("headers") or {}
    for name, header in headers.items():
        if name.lower() == CSP_HEADER and NO_FORMS in header.get("value", ""):
            rule = copy.deepcopy(rule)
            rule["action_parameters"]["headers"][name]["value"] = header["value"].replace(NO_FORMS, SAME_SITE_FORMS)
            return {key: rule[key] for key in ("description", "expression", "action", "action_parameters", "enabled") if key in rule}
    return None


def cloudflare_csp_state(rule):
    headers = (rule.get("action_parameters") or {}).get("headers") or {}
    values = [header.get("value", "") for name, header in headers.items() if name.lower() == CSP_HEADER]
    if not values:
        return None
    return "change" if any(NO_FORMS in value for value in values) else "done" if any(SAME_SITE_FORMS in value for value in values) else "other"


class CloudflareError(Exception):
    pass


class Cloudflare:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            CLOUDFLARE_API + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)["result"]
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            try:
                errors = json.loads(error.read() or b"{}").get("errors", [])
            except ValueError:
                errors = []
            detail = "; ".join(item.get("message", "") for item in errors) or error.reason
            raise CloudflareError(f"Cloudflare {method} {path.split('?')[0]}: {error.code} {detail}") from None

    def zone_id(self, name):
        zones = self.call("GET", f"/zones?name={name}") or []
        if not zones:
            sys.exit(f"Cloudflare: no zone named {name} for this token. Give the token Zone Read on {name}.")
        return zones[0]["id"]

    def entrypoint(self, zone, phase):
        return self.call("GET", f"/zones/{zone}/rulesets/phases/{phase}/entrypoint")

    def put_rule(self, zone, phase, ruleset, rule, rule_id):
        if ruleset is None:
            self.call("PUT", f"/zones/{zone}/rulesets/phases/{phase}/entrypoint", {"rules": [rule]})
        elif rule_id is None:
            self.call("POST", f"/zones/{zone}/rulesets/{ruleset['id']}/rules", rule)
        else:
            self.call("PATCH", f"/zones/{zone}/rulesets/{ruleset['id']}/rules/{rule_id}", rule)


# ---- running it ----


class Step:
    def __init__(self, label, run):
        self.label, self.run = label, run


def terraform_outputs():
    try:
        result = subprocess.run(["terraform", f"-chdir={ROOT / 'infra'}", "output", "-json"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"Couldn't read the Terraform outputs (run terraform apply first?): {getattr(error, 'stderr', '') or error}")
    values = {key: output["value"] for key, output in json.loads(result.stdout).items()}
    missing = [key for key in ("cloudfront_origin_domain", "origin_secret", "edge_secret") if not values.get(key)]
    if missing:
        sys.exit(f"Missing Terraform outputs: {', '.join(missing)}. Run terraform apply first.")
    return values


def managed_policy_id(items, wrapper, config_key, name):
    for item in items:
        policy = item[wrapper]
        if policy[config_key]["Name"] == f"Managed-{name}":
            return policy["Id"]
    sys.exit(f"CloudFront has no managed policy named Managed-{name}.")


def cloudfront_steps(cloudfront, values):
    """The CloudFront changes, and where the CSP stands ('change', 'done', 'other', 'missing' or 'managed')."""
    response = cloudfront.get_distribution_config(Id=DISTRIBUTION)
    cache_policy = managed_policy_id(
        cloudfront.list_cache_policies(Type="managed", MaxItems="100")["CachePolicyList"].get("Items", []),
        "CachePolicy", "CachePolicyConfig", "CachingDisabled",
    )
    request_policy = managed_policy_id(
        cloudfront.list_origin_request_policies(Type="managed", MaxItems="100")["OriginRequestPolicyList"].get("Items", []),
        "OriginRequestPolicy", "OriginRequestPolicyConfig", "AllViewerExceptHostHeader",
    )
    config, changes = plan_distribution(response["DistributionConfig"], values["cloudfront_origin_domain"], values["origin_secret"], cache_policy, request_policy)
    steps = []
    if changes:
        steps.append(Step("\n  ".join(changes), lambda: cloudfront.update_distribution(Id=DISTRIBUTION, IfMatch=response["ETag"], DistributionConfig=config)))

    policy_id = response["DistributionConfig"]["DefaultCacheBehavior"].get("ResponseHeadersPolicyId")
    if not policy_id:
        return steps, "missing"
    policy = cloudfront.get_response_headers_policy_config(Id=policy_id)
    name = policy["ResponseHeadersPolicyConfig"].get("Name", policy_id)
    updated, state = plan_headers_policy(policy["ResponseHeadersPolicyConfig"])
    if updated is not None:
        if name.startswith("Managed-"):
            return steps, "managed"
        steps.append(
            Step(
                f"~ CSP in response headers policy '{name}': {NO_FORMS} → {SAME_SITE_FORMS}",
                lambda: cloudfront.update_response_headers_policy(Id=policy_id, IfMatch=policy["ETag"], ResponseHeadersPolicyConfig=updated),
            )
        )
    return steps, state


def cloudflare_steps(cf, values, csp_state):
    zone = cf.zone_id(ZONE)
    steps = []
    for phase, rule in cloudflare_rules(values["edge_secret"]):
        ruleset = cf.entrypoint(zone, phase)
        action, rule_id = plan_rule(ruleset, rule)
        name = rule["description"]
        if action is None:
            print(f"  = {name}: already in place")
            continue
        label = f"{'+' if action == 'add' else '~'} rule '{name}'" + (f": X-Edge-Verify on /api/ ({hidden(values['edge_secret'])})" if phase == "http_request_late_transform" else "")
        steps.append(Step(label, lambda phase=phase, ruleset=ruleset, rule=rule, rule_id=rule_id: apply_rule(cf, zone, phase, ruleset, rule, rule_id)))

    if csp_state in ("missing", "managed"):
        ruleset = cf.entrypoint(zone, "http_response_headers_transform")
        for rule in (ruleset or {}).get("rules", []):
            state = cloudflare_csp_state(rule)
            if state is None:
                continue
            csp_state = state
            updated = plan_cloudflare_csp(rule)
            if updated:
                label = f"~ CSP in Cloudflare response header rule '{rule.get('description', rule['id'])}': {NO_FORMS} → {SAME_SITE_FORMS}"
                steps.append(Step(label, lambda rs=ruleset, rule=rule, updated=updated: cf.put_rule(zone, None, rs, updated, rule["id"])))
    return steps, csp_state


def apply_rule(cf, zone, phase, ruleset, rule, rule_id):
    try:
        cf.put_rule(zone, phase, ruleset, rule, rule_id)
    except CloudflareError as error:
        if phase != "http_ratelimit":
            raise
        print(f"    Cloudflare refused the rate limit rule ({error}). Trying the plain version: {RATE_LIMIT_FALLBACK}")
        cf.put_rule(zone, phase, ruleset, {**rule, "expression": RATE_LIMIT_FALLBACK}, rule_id)


def check_api():
    """A working edge answers GET /api/confirm (no token) with a redirect to the confirm page."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    request = urllib.request.Request(f"{SITE}/api/confirm", headers={"User-Agent": "adan-cloud-edge-setup/1"})
    try:
        urllib.request.build_opener(NoRedirect).open(request, timeout=20)
        status, location, body = 200, "", ""
    except urllib.error.HTTPError as error:
        status, location = error.code, error.headers.get("Location", "")
        body = error.read(200).decode("utf-8", "replace").strip()
    if status in (301, 302, 303) and location.endswith("/newsletter/confirm"):
        print(f"OK: {SITE}/api/confirm reaches the backend through Cloudflare and CloudFront.")
        return 0
    if status == 403 and body == "Forbidden":
        hint = "the backend refused it: the Cloudflare header (X-Edge-Verify) or the CloudFront header (X-Origin-Verify) isn't arriving"
    elif status == 403:
        hint = "Cloudflare or CloudFront blocked the request before the backend"
    elif status == 404:
        # The distribution's custom error pages turn the backend's 403 into the site's 404 page too.
        hint = (
            "either /api/* isn't routed to the backend yet (CloudFront may still be deploying: try --check in a few "
            "minutes), or the backend refused it and CloudFront showed its 404 page: check the X-Edge-Verify and "
            "X-Origin-Verify headers"
        )
    else:
        hint = "unexpected answer"
    print(f"Not working yet: {status} {location} ({hint}).")
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the changes (without it, only shows them)")
    parser.add_argument("--check", action="store_true", help="only test /api through the edge")
    args = parser.parse_args()
    if args.check:
        sys.exit(check_api())

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    values = terraform_outputs()
    cloudfront = boto3.client("cloudfront")
    try:
        print(f"CloudFront {DISTRIBUTION}")
        steps, csp_state = cloudfront_steps(cloudfront, values)
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if token:
            print(f"Cloudflare {ZONE}")
            more, csp_state = cloudflare_steps(Cloudflare(token), values, csp_state)
            steps += more
        else:
            print("Cloudflare: skipped (set CLOUDFLARE_API_TOKEN to include it)")
    except (CloudflareError, ClientError, BotoCoreError) as error:
        sys.exit(f"Couldn't read the current setup: {error}")

    for step in steps:
        print(f"  {step.label}")
    if csp_state == "done":
        print(f"  = CSP already has {SAME_SITE_FORMS}")
    elif csp_state not in ("change",):
        print(f"  ! CSP: couldn't find {NO_FORMS} to change. Change it to {SAME_SITE_FORMS} by hand where you set the CSP.")

    if not steps:
        print("Nothing to change.")
    elif not args.apply:
        print("\nDry run: nothing changed. Run again with --apply to make these changes.")
        return
    else:
        for step in steps:
            try:
                step.run()
            except (CloudflareError, ClientError, BotoCoreError) as error:
                sys.exit(f"Stopped at: {step.label.splitlines()[0]}\n  {error}\nFix that, then run it again: it only redoes what's still missing.")
        print("\nApplied.")

    if args.apply or not steps:
        print("Waiting for CloudFront to finish deploying (a few minutes)...")
        cloudfront.get_waiter("distribution_deployed").wait(Id=DISTRIBUTION, WaiterConfig={"Delay": 20, "MaxAttempts": 45})
        sys.exit(check_api())


if __name__ == "__main__":
    main()
