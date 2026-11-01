output "dns_records" {
  description = "Add these in Cloudflare, as DNS only (grey cloud), so SES can sign and send for the domain."
  value = concat(
    [for token in aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens : {
      type  = "CNAME"
      name  = "${token}._domainkey.${var.domain}"
      value = "${token}.dkim.amazonses.com"
    }],
    [
      {
        type  = "MX"
        name  = aws_sesv2_email_identity_mail_from_attributes.domain.mail_from_domain
        value = "10 feedback-smtp.${var.region}.amazonses.com"
      },
      {
        type  = "TXT"
        name  = aws_sesv2_email_identity_mail_from_attributes.domain.mail_from_domain
        value = "v=spf1 include:amazonses.com ~all"
      },
    ],
  )
}

output "cloudfront_origin_domain" {
  description = "Origin domain for the /api/* behavior on the site's CloudFront distribution."
  value       = trimprefix(aws_apigatewayv2_api.signup.api_endpoint, "https://")
}

output "origin_secret" {
  description = "Value for the X-Origin-Verify custom header on that origin. Read it with: terraform output -raw origin_secret"
  value       = random_password.origin_secret.result
  sensitive   = true
}

output "edge_secret" {
  description = "Value for the X-Edge-Verify header that the Cloudflare Transform Rule adds. Read it with: terraform output -raw edge_secret"
  value       = random_password.edge_secret.result
  sensitive   = true
}

output "table_name" {
  value = aws_dynamodb_table.newsletter.name
}

output "contact_list" {
  value = aws_sesv2_contact_list.readers.contact_list_name
}
