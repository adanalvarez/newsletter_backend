# Anonymous answers to polls and to "Was this edition useful?" (POST /api/vote, lambda_code/newsletter/feedback.py).
# Its own function and role: it can count votes and store comments, read the list of open questions, and
# nothing else. It can't send email or read subscribers.

# Key for the daily, one-way voter code that stops repeat votes.
resource "random_password" "vote_key" {
  length  = 40
  special = false
}

resource "aws_lambda_function" "feedback" {
  function_name = "${var.project}-feedback"
  runtime       = "python3.13"
  handler       = "newsletter.feedback.lambda_handler"
  role          = aws_iam_role.feedback.arn
  timeout       = 10
  memory_size   = 256

  filename         = data.archive_file.newsletter.output_path
  source_code_hash = data.archive_file.newsletter.output_base64sha256

  environment {
    variables = {
      TABLE_NAME             = aws_dynamodb_table.newsletter.name
      SITE_URL               = var.site_url
      ORIGIN_SECRETS         = join(",", concat([random_password.origin_secret.result], var.previous_origin_secrets))
      EDGE_SECRETS           = var.require_edge_secret ? join(",", concat([random_password.edge_secret.result], var.previous_origin_secrets)) : ""
      VOTE_KEY               = random_password.vote_key.result
      POLLS_BUCKET           = var.site_bucket
      POLLS_KEY              = "newsletter/polls.json"
      COMMENT_RETENTION_DAYS = tostring(var.comment_retention_days)
    }
  }

  depends_on = [aws_cloudwatch_log_group.feedback]
}

resource "aws_cloudwatch_log_group" "feedback" {
  name              = "/aws/lambda/${var.project}-feedback"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "feedback" {
  name               = "${var.project}-feedback-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "feedback" {
  name = "${var.project}-feedback-policy"
  role = aws_iam_role.feedback.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.feedback.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.newsletter.arn
        # Only vote, voter and comment items: never subscribers or tokens.
        Condition = {
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = ["POLL#*", "VOTER#*", "COMMENT#*"]
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.site_bucket}/newsletter/polls.json"
      },
    ]
  })
}

resource "aws_apigatewayv2_integration" "feedback" {
  api_id                 = aws_apigatewayv2_api.signup.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.feedback.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "vote" {
  api_id    = aws_apigatewayv2_api.signup.id
  route_key = "POST /api/vote"
  target    = "integrations/${aws_apigatewayv2_integration.feedback.id}"
}

resource "aws_lambda_permission" "feedback_api" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.feedback.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.signup.execution_arn}/*/*"
}
