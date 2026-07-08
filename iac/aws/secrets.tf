# Single source of truth for runtime secrets: SSM SecureString parameters.
# Terraform owns their existence and naming; VALUES are set manually (see
# iac/README.md) and never touched by applies (ignore_changes below).
locals {
  ssm_prefix = "/projects/autorater"
  secrets = [
    "OPENROUTER_API_KEY",    # miner: LLM-as-Judge via OpenRouter
    "SLACK_WEBHOOK_URL",     # alerting: Slack incoming webhook
    "PAGERDUTY_ROUTING_KEY", # alerting: PagerDuty Events v2 routing key
  ]
}

resource "aws_ssm_parameter" "secret" {
  for_each = toset(local.secrets)

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = "CHANGE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

# Task execution roles pull these at container start.
resource "aws_iam_role_policy" "task_execution_ssm" {
  name = "read-project-secrets"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/*"
    }]
  })
}
