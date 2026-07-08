# Integrated observability/tracing hub: CloudWatch logs + metrics feed the
# dashboards; X-Ray traces the miner → judge → alerting call path. All of it
# inherits the default_tags, so observability spend itself is attributable.
resource "aws_cloudwatch_log_group" "miner" {
  name              = "/ecs/${var.app_name}-${var.env}/miner"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "alerting" {
  name              = "/ecs/${var.app_name}-${var.env}/alerting"
  retention_in_days = 30
}

resource "aws_xray_sampling_rule" "pipeline" {
  rule_name      = "${var.app_name}-${var.env}"
  priority       = 100
  version        = 1
  reservoir_size = 5
  fixed_rate     = 0.10 # trace 10% of sweeps; enough to profile, cheap to keep
  host           = "*"
  http_method    = "*"
  url_path       = "*"
  resource_arn   = "*"
  service_name   = "${var.app_name}-*"
  service_type   = "*"
}

resource "aws_xray_group" "pipeline" {
  group_name        = "${var.app_name}-${var.env}"
  filter_expression = "service(begin_with(\"${var.app_name}\"))"
}

# Regression alerts dispatched — the signal the on-call watches. Matches
# the alerting service's event NAME (compatibility contract with
# handler.go's alert_dispatched call — see .plan/standardized-logging.md).
resource "aws_cloudwatch_log_metric_filter" "alerts_dispatched" {
  name           = "${var.app_name}-${var.env}-alerts-dispatched"
  log_group_name = aws_cloudwatch_log_group.alerting.name
  pattern        = "{ $.msg = \"alert_dispatched\" }"

  metric_transformation {
    name      = "AlertsDispatched"
    namespace = "Autorater/${var.env}"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "alert_storm" {
  alarm_name          = "${var.app_name}-${var.env}-alert-storm"
  alarm_description   = "Regression alert volume abnormally high — likely a bad model/prompt deploy"
  namespace           = "Autorater/${var.env}"
  metric_name         = "AlertsDispatched"
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
