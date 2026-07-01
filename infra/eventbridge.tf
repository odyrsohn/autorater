# Serverless cron engine: EventBridge fires the mining sweep on a schedule
# instead of keeping a poller warm 24/7 (see docs/finops-policy.md).
resource "aws_cloudwatch_event_rule" "miner_schedule" {
  name                = "${var.app_name}-${var.env}-miner-sweep"
  description         = "Launches the evaluation-mining Fargate task"
  schedule_expression = var.miner_schedule
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
  name = "run-miner-task"
  role = aws_iam_role.events_run_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = aws_ecs_task_definition.miner.arn
        Condition = {
          ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn }
        }
      },
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = [aws_iam_role.task_execution.arn, aws_iam_role.miner_task.arn]
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_cloudwatch_event_target" "miner" {
  rule     = aws_cloudwatch_event_rule.miner_schedule.name
  arn      = aws_ecs_cluster.this.arn
  role_arn = aws_iam_role.events_run_task.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.miner.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets         = var.vpc_subnet_ids
      security_groups = var.vpc_security_group_ids
    }
  }
}
