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

  # No server_side_encryption block: the table is still encrypted at rest, with DynamoDB's own (AWS-owned) key.
  # Asking for the AWS-managed KMS key (aws/dynamodb) failed on the first apply because that key didn't exist
  # in the account yet, and it adds KMS calls and cost for no real gain here.
}
