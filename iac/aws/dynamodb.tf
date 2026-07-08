# Durable miner sweep state: per-source cursors (StartAfter resume) and the
# single-runner lease. Conditional writes on the lease item guarantee only
# one mining task processes at a time even if EventBridge overlaps launches.
resource "aws_dynamodb_table" "miner_state" {
  name         = "${var.app_name}-${var.env}-miner-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_iam_role_policy" "miner_state" {
  name = "miner-state"
  role = aws_iam_role.miner_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
      ]
      Resource = aws_dynamodb_table.miner_state.arn
    }]
  })
}
