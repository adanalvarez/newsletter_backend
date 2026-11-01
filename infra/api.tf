# HTTP API in front of the sign-up function. CloudFront sends adan.cloud/api/* here (set up by hand on the
# site's distribution; see the README). API Gateway's throttling is the second limit, after Cloudflare's.

resource "aws_apigatewayv2_api" "signup" {
  name          = "${var.project}-signup"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "signup" {
  api_id                 = aws_apigatewayv2_api.signup.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.signup.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "signup" {
  for_each = toset(["POST /api/subscribe", "POST /api/confirm", "GET /api/confirm"])

  api_id    = aws_apigatewayv2_api.signup.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.signup.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.signup.id
  name        = "$default"
  auto_deploy = true

  # A safety ceiling against runaway cost, not the rate limiter: this throttle is shared by every client, so
  # per-visitor limits are Cloudflare's job (the edge secret makes sure requests go through Cloudflare).
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.signup.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.signup.execution_arn}/*/*"
}
