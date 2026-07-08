# Serverless cron engine: EventBridge fires the miner-sweep state machine
# (step_functions.tf) on a schedule instead of keeping a poller warm 24/7
# (see docs/finops-policy.md). Set miner_schedule_enabled = false (dev
# default) to disable automatic firing; a sweep can still be started
# manually with `aws stepfunctions start-execution --state-machine-arn
# <miner_sweep_state_machine_arn output>`.
resource "aws_cloudwatch_event_rule" "miner_schedule" {
  name                = "${var.app_name}-${var.env}-miner-sweep"
  description         = "Launches the evaluation-mining sweep orchestration"
  schedule_expression = var.miner_schedule
  state               = var.miner_schedule_enabled ? "ENABLED" : "DISABLED"
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events_run_task" {
  name               = "${var.app_name}-${var.env}-events-run-task"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

resource "aws_iam_role_policy" "events_run_task" {
  name = "start-miner-sweep"
  role = aws_iam_role.events_run_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.miner_sweep.arn
      },
    ]
  })
}

resource "aws_cloudwatch_event_target" "miner" {
  rule     = aws_cloudwatch_event_rule.miner_schedule.name
  arn      = aws_sfn_state_machine.miner_sweep.arn
  role_arn = aws_iam_role.events_run_task.arn
}
