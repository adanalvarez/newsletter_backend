# Sending identity: the whole domain, with DKIM, and a MAIL FROM subdomain so SPF aligns.
# The DNS records these need are in the `dns_records` output; add them in Cloudflare (DNS only, not proxied).

# Two configuration sets: one for editions, one for confirmation emails. Confirmations are what a flood of fake
# sign-ups would produce, so they can be paused on their own (see the README) without stopping editions.
locals {
  configuration_sets = {
    editions      = var.project
    confirmations = "${var.project}-confirmations"
  }
}

resource "aws_sesv2_configuration_set" "sets" {
  for_each               = local.configuration_sets
  configuration_set_name = each.value

  delivery_options {
    tls_policy = "REQUIRE"
  }

  reputation_options {
    reputation_metrics_enabled = true
  }

  sending_options {
    sending_enabled = true
  }

  suppression_options {
    suppressed_reasons = ["BOUNCE", "COMPLAINT"]
  }
}

# No default configuration set on the identity: the newsletter code names its set on every send, so other
# mail from the domain (for example replies sent from adan@adan.cloud through SES SMTP) stays out of it.
resource "aws_sesv2_email_identity" "domain" {
  email_identity = var.domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

resource "aws_sesv2_email_identity_mail_from_attributes" "domain" {
  email_identity         = aws_sesv2_email_identity.domain.email_identity
  mail_from_domain       = "${var.mail_from_subdomain}.${var.domain}"
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

# One list for everything (decided 2026-11-01): readers choose topics, there are no separate lists.
# SES hosts the unsubscribe page and adds one-click unsubscribe headers to every edition sent to this list.
resource "aws_sesv2_contact_list" "readers" {
  contact_list_name = var.project
  description       = "Readers of No Hype, No Fear"

  topic {
    topic_name                  = "newsletter"
    display_name                = "No Hype, No Fear"
    description                 = "The monthly newsletter"
    default_subscription_status = "OPT_OUT"
  }
}

# Bounces, complaints and unsubscribes go to EventBridge, where the ses-events Lambda picks them up.
# Editions also report SEND and DELIVERY, for the per-edition numbers (tools/cli.py stats). No OPEN or
# CLICK events: those need tracking pixels and rewritten links, which the newsletter promises not to use.
locals {
  event_types = {
    editions      = ["SEND", "DELIVERY", "BOUNCE", "COMPLAINT", "REJECT", "SUBSCRIPTION"]
    confirmations = ["BOUNCE", "COMPLAINT", "REJECT"]
  }
}

resource "aws_sesv2_configuration_set_event_destination" "eventbridge" {
  for_each               = aws_sesv2_configuration_set.sets
  configuration_set_name = each.value.configuration_set_name
  event_destination_name = "eventbridge"

  event_destination {
    enabled              = true
    matching_event_types = local.event_types[each.key]

    event_bridge_destination {
      event_bus_arn = "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:event-bus/default"
    }
  }
}
