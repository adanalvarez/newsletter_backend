# Subscribers, pending confirmations, the consent log and the daily counter (see lambda_code/newsletter/store.py).

resource "aws_dynamodb_table" "newsletter" {
  name         = var.project
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  # This table is the proof of consent: don't let a mistaken apply or a stray click delete it.
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }

  # Pending sign-ups, used confirmation links and old counters expire on their own.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}
