# Early warnings, well below the levels where AWS reviews the account (bounces 5%, complaints 0.1%).

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "bounce_rate" {
  alarm_name          = "${var.project}-ses-bounce-rate"
  alarm_description   = "SES bounce rate above 2%. AWS starts reviewing the account at 5%."
  namespace           = "AWS/SES"
  metric_name         = "Reputation.BounceRate"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0.02
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "complaint_rate" {
  alarm_name          = "${var.project}-ses-complaint-rate"
  alarm_description   = "SES complaint rate above 0.05%. AWS starts reviewing the account at 0.1%."
  namespace           = "AWS/SES"
  metric_name         = "Reputation.ComplaintRate"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0.0005
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = {
    signup     = aws_lambda_function.signup.function_name
    ses-events = aws_lambda_function.ses_events.function_name
    feedback   = aws_lambda_function.feedback.function_name
  }

  alarm_name          = "${var.project}-${each.key}-errors"
  alarm_description   = "The ${each.key} function failed. Readers may not be able to subscribe or confirm."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# The daily ceiling on confirmation emails was reached: either a real spike (raise the limit for the day) or
# someone flooding the form with addresses (see the README: pause confirmations, add a challenge).
resource "aws_cloudwatch_log_metric_filter" "daily_ceiling" {
  name           = "${var.project}-daily-ceiling-reached"
  log_group_name = aws_cloudwatch_log_group.signup.name
  pattern        = "DAILY_CEILING_REACHED"

  metric_transformation {
    name      = "DailyCeilingReached"
    namespace = "Newsletter"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "daily_ceiling" {
  alarm_name          = "${var.project}-daily-ceiling-reached"
  alarm_description   = "Confirmation emails hit the daily ceiling, so real sign-ups are getting no email. Real spike or a flood of fake sign-ups?"
  namespace           = "Newsletter"
  metric_name         = "DailyCeilingReached"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Someone confirmed but their welcome email couldn't be sent. They're subscribed either way; the log line says
# which SES error it was.
resource "aws_cloudwatch_log_metric_filter" "welcome_failed" {
  name           = "${var.project}-welcome-failed"
  log_group_name = aws_cloudwatch_log_group.signup.name
  pattern        = "WELCOME_FAILED"

  metric_transformation {
    name      = "WelcomeFailed"
    namespace = "Newsletter"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "welcome_failed" {
  alarm_name          = "${var.project}-welcome-failed"
  alarm_description   = "A new reader confirmed but didn't get the welcome email. See the signup function's log for the SES error."
  namespace           = "Newsletter"
  metric_name         = "WelcomeFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "ses_events_dlq" {
  alarm_name          = "${var.project}-ses-events-dlq"
  alarm_description   = "An SES event (bounce, complaint or unsubscribe) couldn't be processed and is waiting in the dead-letter queue."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.ses_events_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
