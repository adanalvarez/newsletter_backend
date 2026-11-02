import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("edge_setup", Path(__file__).resolve().parents[1] / "scripts" / "edge_setup.py")
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)

SECRET = "o" * 40
DOMAIN = "abc123.execute-api.eu-west-1.amazonaws.com"


def distribution():
    return {
        "Origins": {"Quantity": 1, "Items": [{"Id": "s3-site", "DomainName": "adanalvarez.s3.amazonaws.com"}]},
        "DefaultCacheBehavior": {
            "TargetOriginId": "s3-site",
            "ResponseHeadersPolicyId": "headers-1",
            "FunctionAssociations": {"Quantity": 1, "Items": [{"FunctionARN": "arn:fn", "EventType": "viewer-request"}]},
        },
        "CacheBehaviors": {"Quantity": 0},
    }


def plan(config, secret=SECRET):
    return edge.plan_distribution(config, DOMAIN, secret, "cache-disabled", "all-viewer-except-host")


class CloudFrontTest(unittest.TestCase):
    def test_adds_the_origin_and_a_first_behavior_without_a_function(self):
        config, changes = plan(distribution())
        self.assertEqual(len(changes), 2)
        origin = config["Origins"]["Items"][1]
        self.assertEqual((config["Origins"]["Quantity"], origin["Id"], origin["DomainName"]), (2, "newsletter-api", DOMAIN))
        self.assertEqual(origin["CustomHeaders"]["Items"], [{"HeaderName": "X-Origin-Verify", "HeaderValue": SECRET}])
        self.assertEqual(origin["CustomOriginConfig"]["OriginProtocolPolicy"], "https-only")
        behavior = config["CacheBehaviors"]["Items"][0]
        self.assertEqual(config["CacheBehaviors"]["Quantity"], 1)
        self.assertEqual((behavior["PathPattern"], behavior["TargetOriginId"]), ("/api/*", "newsletter-api"))
        self.assertEqual(behavior["FunctionAssociations"], {"Quantity": 0})
        self.assertIn("POST", behavior["AllowedMethods"]["Items"])
        self.assertEqual((behavior["CachePolicyId"], behavior["OriginRequestPolicyId"]), ("cache-disabled", "all-viewer-except-host"))
        self.assertEqual(behavior["ResponseHeadersPolicyId"], "headers-1")
        # The site's own behavior is untouched.
        self.assertEqual(config["DefaultCacheBehavior"], distribution()["DefaultCacheBehavior"])

    def test_a_second_run_changes_nothing(self):
        config, _ = plan(distribution())
        again, changes = plan(config)
        self.assertEqual(changes, [])
        self.assertEqual(again, config)

    def test_a_new_secret_updates_the_header(self):
        config, _ = plan(distribution())
        updated, changes = plan(config, secret="n" * 40)
        self.assertEqual(len(changes), 1)
        self.assertEqual(updated["Origins"]["Items"][1]["CustomHeaders"]["Items"][0]["HeaderValue"], "n" * 40)

    def test_an_existing_api_behavior_loses_its_function(self):
        config, _ = plan(distribution())
        config["CacheBehaviors"]["Items"][0]["FunctionAssociations"] = {"Quantity": 1, "Items": [{"FunctionARN": "arn:fn"}]}
        updated, changes = plan(config)
        self.assertEqual(len(changes), 1)
        self.assertEqual(updated["CacheBehaviors"]["Items"][0]["FunctionAssociations"], {"Quantity": 0})

    def test_secrets_never_appear_in_what_it_prints(self):
        _, changes = plan(distribution())
        self.assertFalse(any(SECRET in change for change in changes))

    def test_csp_in_the_security_headers(self):
        policy = {"Name": "site", "SecurityHeadersConfig": {"ContentSecurityPolicy": {"Override": True, "ContentSecurityPolicy": "default-src 'self'; form-action 'none'; frame-ancestors 'none'"}}}
        updated, state = edge.plan_headers_policy(policy)
        self.assertEqual(state, "change")
        self.assertEqual(updated["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"], "default-src 'self'; form-action 'self'; frame-ancestors 'none'")
        self.assertEqual(edge.plan_headers_policy(updated), (None, "done"))

    def test_csp_as_a_custom_header_and_no_csp(self):
        policy = {"CustomHeadersConfig": {"Quantity": 1, "Items": [{"Header": "Content-Security-Policy", "Value": "form-action 'none'", "Override": True}]}}
        updated, state = edge.plan_headers_policy(policy)
        self.assertEqual((state, updated["CustomHeadersConfig"]["Items"][0]["Value"]), ("change", "form-action 'self'"))
        self.assertEqual(edge.plan_headers_policy({"Name": "x"}), (None, "missing"))


class CloudflareTest(unittest.TestCase):
    def rules(self, secret="e" * 40):
        return dict((rule["description"], rule) for _, rule in edge.cloudflare_rules(secret))

    def test_adds_missing_rules_and_leaves_matching_ones(self):
        wanted = self.rules()["newsletter api edge secret"]
        self.assertEqual(edge.plan_rule(None, wanted), ("add", None))
        existing = {"id": "r1", "rules": [{**wanted, "id": "rule-1", "version": "3", "ref": "x"}]}
        self.assertEqual(edge.plan_rule(existing, wanted), (None, "rule-1"))
        self.assertEqual(edge.plan_rule(existing, self.rules("f" * 40)["newsletter api edge secret"]), ("update", "rule-1"))

    def test_rate_limit_in_the_plain_version_counts_as_in_place(self):
        wanted = self.rules()["newsletter api rate limit"]
        existing = {"id": "r1", "rules": [{**wanted, "id": "rule-2", "expression": edge.RATE_LIMIT_FALLBACK,
                                           "ratelimit": {**wanted["ratelimit"], "characteristics": ["cf.colo.id", "ip.src"], "requests_to_origin": False}}]}
        self.assertEqual(edge.plan_rule(existing, wanted), (None, "rule-2"))

    def test_csp_in_a_response_header_rule(self):
        rule = {"id": "h1", "version": "2", "description": "security headers", "expression": "true", "action": "rewrite", "enabled": True,
                "action_parameters": {"headers": {"Content-Security-Policy": {"operation": "set", "value": "default-src 'self'; form-action 'none'"}}}}
        self.assertEqual(edge.cloudflare_csp_state(rule), "change")
        updated = edge.plan_cloudflare_csp(rule)
        self.assertEqual(updated["action_parameters"]["headers"]["Content-Security-Policy"]["value"], "default-src 'self'; form-action 'self'")
        self.assertNotIn("id", updated)
        self.assertIsNone(edge.plan_cloudflare_csp(updated))


if __name__ == "__main__":
    unittest.main()
