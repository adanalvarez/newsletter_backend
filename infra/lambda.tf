# Two functions from one package: the sign-up endpoints and the SES events processor.

data "archive_file" "newsletter" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda_code"
  output_path = "${path.module}/../build/newsletter.zip"
  excludes    = ["**/__pycache__/**"]
}

# CloudFront adds X-Origin-Verify (origin_secret) and Cloudflare adds X-Edge-Verify (edge_secret) to every
# /api request. The function refuses anything without both, so neither API Gateway's own URL nor a request sent
# straight to CloudFront can skip Cloudflare's rate limits.
resource "random_password" "origin_secret" {
  length  = 40
  special = false
}

resource "random_password" "edge_secret" {
  length  = 40
  special = false
}

# Key for the hashes that stand in for email addresses in logs, so they can't be matched against known addresses.
resource "random_password" "log_hash_key" {
  length  = 40
  special = false
}

locals {
  # The bare address from "Name <address>", for the IAM condition that pins the sender.
  from_email = regex("[^<>\\s\"]+@[^<>\\s\"]+", var.from_address)

  common_environment = {
    TABLE_NAME                 = aws_dynamodb_table.newsletter.name
    CONTACT_LIST               = aws_sesv2_contact_list.readers.contact_list_name
    TOPIC                      = "newsletter"
    SITE_URL                   = var.site_url
    LOG_HASH_KEY               = random_password.log_hash_key.result
    RETENTION_DAYS             = tostring(var.retention_days)
    UNCONFIRMED_RETENTION_DAYS = tostring(var.unconfirmed_retention_days)
  }
}

# ---- Sign-up ----

resource "aws_lambda_function" "signup" {
  function_name = "${var.project}-signup"
  runtime       = "python3.13"
  handler       = "newsletter.signup.lambda_handler"
  role          = aws_iam_role.signup.arn
  timeout       = 10
  memory_size   = 256

  filename         = data.archive_file.newsletter.output_path
  source_code_hash = data.archive_file.newsletter.output_base64sha256

  environment {
    variables = merge(local.common_environment, {
      CONFIRMATION_CONFIGURATION_SET = aws_sesv2_configuration_set.sets["confirmations"].configuration_set_name
      FROM_ADDRESS                   = var.from_address
      REPLY_TO                       = var.reply_to
      ORIGIN_SECRETS                 = join(",", concat([random_password.origin_secret.result], var.previous_origin_secrets))
      EDGE_SECRETS                   = var.require_edge_secret ? join(",", concat([random_password.edge_secret.result], var.previous_origin_secrets)) : ""
      CONSENT_VERSIONS               = jsonencode(var.consent_versions)
      ROLES                          = jsonencode(var.roles)
      DAILY_CONFIRMATION_LIMIT       = tostring(var.daily_confirmation_limit)
    })
  }

  depends_on = [aws_cloudwatch_log_group.signup]
}

resource "aws_cloudwatch_log_group" "signup" {
  name              = "/aws/lambda/${var.project}-signup"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "signup" {
  name               = "${var.project}-signup-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "signup" {
  name = "${var.project}-signup-policy"
  role = aws_iam_role.signup.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.signup.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.newsletter.arn
      },
      {
        # Confirmation emails only: from the newsletter's own address, through the confirmations
        # configuration set. Even with this role's credentials, nobody can send as another @domain address.
        Effect = "Allow"
        Action = ["ses:SendEmail"]
        Resource = [
          aws_sesv2_email_identity.domain.arn,
          aws_sesv2_configuration_set.sets["confirmations"].arn,
        ]
        Condition = {
          StringEquals = { "ses:FromAddress" = local.from_email }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ses:CreateContact", "ses:UpdateContact"]
        Resource = aws_sesv2_contact_list.readers.arn
      },
    ]
  })
}

# ---- SES events ----

resource "aws_lambda_function" "ses_events" {
  function_name = "${var.project}-ses-events"
  runtime       = "python3.13"
  handler       = "newsletter.ses_events.lambda_handler"
  role          = aws_iam_role.ses_events.arn
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.newsletter.output_path
  source_code_hash = data.archive_file.newsletter.output_base64sha256

  environment {
    variables = local.common_environment
  }

  depends_on = [aws_cloudwatch_log_group.ses_events]
}

# If processing an event still fails after retries, it goes to the dead-letter queue (and raises an alarm)
# instead of disappearing, so no unsubscribe or bounce is lost.
resource "aws_lambda_function_event_invoke_config" "ses_events" {
  function_name          = aws_lambda_function.ses_events.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.ses_events_dlq.arn
    }
  }
}

resource "aws_cloudwatch_log_group" "ses_events" {
  name              = "/aws/lambda/${var.project}-ses-events"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "ses_events" {
  name               = "${var.project}-ses-events-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ses_events" {
  name = "${var.project}-ses-events-policy"
  role = aws_iam_role.ses_events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.ses_events.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.newsletter.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ses:DeleteContact"]
        Resource = aws_sesv2_contact_list.readers.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.ses_events_dlq.arn
      },
    ]
  })
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}
