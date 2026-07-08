# Compute cluster: a scale-to-zero alerting webhook service plus a mining
# worker task, both driven on demand by the Step Functions orchestrator
# (step_functions.tf) that EventBridge triggers (eventbridge.tf).
resource "aws_ecs_cluster" "this" {
  name = "${var.app_name}-${var.env}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecr_repository" "service" {
  for_each = toset(["miner", "alerting"])

  name                 = "${var.app_name}-${each.key}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${var.app_name}-${var.env}-task-exec"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "miner_task" {
  name               = "${var.app_name}-${var.env}-miner"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy" "miner_task" {
  name = "miner-runtime"
  role = aws_iam_role.miner_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.data_lake_bucket}", "arn:aws:s3:::${var.data_lake_bucket}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role" "alerting_task" {
  name               = "${var.app_name}-${var.env}-alerting"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy" "alerting_task" {
  name = "alerting-runtime"
  role = aws_iam_role.alerting_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
      Resource = "*"
    }]
  })
}

resource "aws_ecs_task_definition" "alerting" {
  family                   = "${var.app_name}-alerting"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.alerting_task.arn

  container_definitions = jsonencode([{
    name      = "alerting"
    image     = "${aws_ecr_repository.service["alerting"].repository_url}:latest"
    essential = true
    portMappings = [{
      containerPort = 8070
      protocol      = "tcp"
    }]
    environment = [
      { name = "APP_ENV", value = var.env },
    ]
    secrets = [
      { name = "SLACK_WEBHOOK_URL", valueFrom = aws_ssm_parameter.secret["SLACK_WEBHOOK_URL"].arn },
      { name = "PAGERDUTY_ROUTING_KEY", valueFrom = aws_ssm_parameter.secret["PAGERDUTY_ROUTING_KEY"].arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.alerting.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "alerting"
      }
    }
  }])
}

resource "aws_ecs_service" "alerting" {
  name            = "alerting"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.alerting.arn
  # Idle baseline is zero tasks; step_functions.tf scales this to 1 for the
  # duration of a miner sweep and back to 0 when it finishes.
  desired_count = 0
  launch_type   = "FARGATE"

  network_configuration {
    subnets         = var.vpc_subnet_ids
    security_groups = var.vpc_security_group_ids
    # Dev subnets (core-iac/network-autorater.tf) are public with no NAT
    # gateway; a public IP is the task's only route out (ECR/SSM/S3/etc).
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.alerting.arn
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_ecs_task_definition" "miner" {
  family                   = "${var.app_name}-miner"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.miner_task.arn

  container_definitions = jsonencode([{
    name      = "miner"
    image     = "${aws_ecr_repository.service["miner"].repository_url}:latest"
    essential = true
    environment = [
      { name = "DATA_LAKE_BUCKET", value = var.data_lake_bucket },
      { name = "ALERT_WEBHOOK_URL", value = "http://alerting.${var.app_name}.local:8070/v1/alerts" },
      { name = "JUDGE_MODEL", value = var.judge_model },
      { name = "JUDGE_REASONING_EFFORT", value = var.judge_reasoning_effort },
      { name = "CURSOR_TABLE", value = aws_dynamodb_table.miner_state.name },
      { name = "RESULTS_BUCKET", value = aws_s3_bucket.results.bucket },
      { name = "APP_ENV", value = var.env },
    ]
    secrets = [
      { name = "OPENROUTER_API_KEY", valueFrom = aws_ssm_parameter.secret["OPENROUTER_API_KEY"].arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.miner.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "miner"
      }
    }
  }])
}
