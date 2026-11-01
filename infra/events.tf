# SES events for the newsletter's configuration sets only, to the ses-events function.

resource "aws_cloudwatch_event_rule" "ses_events" {
  name        = "${var.project}-ses-events"
  description = "Sends, deliveries, bounces, complaints and unsubscribes from the newsletter's SES configuration sets"

  event_pattern = jsonencode({
    source  = ["aws.ses"]
    account = [data.aws_caller_identity.current.account_id]
    detail = {
      eventType = ["Send", "Delivery", "Bounce", "Complaint", "Subscription"]
      mail = {
        tags = {
          "ses:configuration-set" = [for set in aws_sesv2_configuration_set.sets : set.configuration_set_name]
        }
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "ses_events" {
  rule = aws_cloudwatch_event_rule.ses_events.name
  arn  = aws_lambda_function.ses_events.arn

  retry_policy {
    maximum_event_age_in_seconds = 86400
    maximum_retry_attempts       = 10
  }

  # If EventBridge can't even deliver the event to the function, it lands in the same dead-letter queue.
  dead_letter_config {
    arn = aws_sqs_queue.ses_events_dlq.arn
  }
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ses_events.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ses_events.arn
}

# Events that couldn't be processed. Anything here raises an alarm (alarms.tf); fix the cause, then replay
# them by sending each message body to the ses-events function.
resource "aws_sqs_queue" "ses_events_dlq" {
  name                      = "${var.project}-ses-events-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_policy" "ses_events_dlq" {
  queue_url = aws_sqs_queue.ses_events_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ses_events_dlq.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.ses_events.arn } }
    }]
  })
}
