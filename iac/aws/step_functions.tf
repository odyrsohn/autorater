# On-demand sweep orchestrator: scales the alerting service up, runs one
# miner sweep to completion, then scales alerting back to zero. This is
# what makes the alerting service scale-to-zero safe (ecs.tf) instead of a
# guess about timing — the miner task never starts until alerting is
# actually running. EventBridge (eventbridge.tf) triggers this instead of
# calling ecs:RunTask directly.
#
# Requires a Standard workflow: the ecs:runTask.sync "run a job and wait"
# integration pattern used below isn't available on Express workflows.
locals {
  miner_sweep_definition = {
    Comment = "Scale alerting up, run a miner sweep, scale alerting back down"
    StartAt = "ScaleAlertingUp"
    States = {
      ScaleAlertingUp = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:ecs:updateService"
        Parameters = {
          Cluster      = aws_ecs_cluster.this.arn
          Service      = aws_ecs_service.alerting.name
          DesiredCount = 1
        }
        Next = "WaitForAlertingStable"
      }
      WaitForAlertingStable = {
        Type    = "Wait"
        Seconds = 10
        Next    = "DescribeAlertingService"
      }
      DescribeAlertingService = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:ecs:describeServices"
        Parameters = {
          Cluster  = aws_ecs_cluster.this.arn
          Services = [aws_ecs_service.alerting.name]
        }
        ResultPath = "$.alertingStatus"
        Next       = "IsAlertingStable"
      }
      IsAlertingStable = {
        Type = "Choice"
        Choices = [
          {
            Variable                 = "$.alertingStatus.Services[0].RunningCount"
            NumericGreaterThanEquals = 1
            Next                     = "RunMinerTask"
          }
        ]
        Default = "WaitForAlertingStable"
      }
      RunMinerTask = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Parameters = {
          LaunchType     = "FARGATE"
          Cluster        = aws_ecs_cluster.this.arn
          TaskDefinition = aws_ecs_task_definition.miner.arn
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = var.vpc_subnet_ids
              SecurityGroups = var.vpc_security_group_ids
              AssignPublicIp = "ENABLED"
            }
          }
        }
        ResultPath = "$.minerResult"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "ScaleAlertingDownAfterFailure"
          }
        ]
        Next = "ScaleAlertingDown"
      }
      ScaleAlertingDown = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:ecs:updateService"
        Parameters = {
          Cluster      = aws_ecs_cluster.this.arn
          Service      = aws_ecs_service.alerting.name
          DesiredCount = 0
        }
        End = true
      }
      ScaleAlertingDownAfterFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:ecs:updateService"
        Parameters = {
          Cluster      = aws_ecs_cluster.this.arn
          Service      = aws_ecs_service.alerting.name
          DesiredCount = 0
        }
        Next = "SweepFailed"
      }
      SweepFailed = {
        Type  = "Fail"
        Error = "MinerSweepFailed"
        Cause = "The miner task failed or was stopped; alerting has been scaled back to zero."
      }
    }
  }
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "miner_sweep_sfn" {
  name               = "${var.app_name}-${var.env}-miner-sweep-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

resource "aws_iam_role_policy" "miner_sweep_sfn" {
  name = "miner-sweep-orchestration"
  role = aws_iam_role.miner_sweep_sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = aws_ecs_service.alerting.id
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = aws_ecs_task_definition.miner.arn
        Condition = {
          ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:StopTask", "ecs:DescribeTasks"]
        Resource = "*"
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
      {
        # Required by the ecs:runTask.sync integration pattern: Step
        # Functions manages an AWS-owned EventBridge rule to hear ECS task
        # state-change events and resume the workflow when the task stops.
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"
      },
    ]
  })
}

resource "aws_sfn_state_machine" "miner_sweep" {
  name     = "${var.app_name}-${var.env}-miner-sweep"
  role_arn = aws_iam_role.miner_sweep_sfn.arn
  type     = "STANDARD"

  definition = jsonencode(local.miner_sweep_definition)
}
