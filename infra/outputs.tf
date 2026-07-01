output "ecr_repositories" {
  description = "Push targets for the miner and alerting images"
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}

output "ecs_cluster" {
  description = "Compute cluster name"
  value       = aws_ecs_cluster.this.name
}

output "miner_schedule" {
  description = "Cron expression driving mining sweeps"
  value       = aws_cloudwatch_event_rule.miner_schedule.schedule_expression
}

output "xray_group" {
  description = "X-Ray group filtering the pipeline's traces"
  value       = aws_xray_group.pipeline.group_name
}
