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

output "miner_sweep_state_machine_arn" {
  description = "Step Functions orchestrator (scale alerting up -> run miner -> scale alerting down). Trigger manually with `aws stepfunctions start-execution --state-machine-arn <this>` when miner_schedule_enabled = false."
  value       = aws_sfn_state_machine.miner_sweep.arn
}

output "xray_group" {
  description = "X-Ray group filtering the pipeline's traces"
  value       = aws_xray_group.pipeline.group_name
}

output "results_bucket" {
  description = "Judged-case results lake (RESULTS_BUCKET for the miner)"
  value       = aws_s3_bucket.results.bucket
}

output "athena_workgroup" {
  description = "Workgroup holding the canned judged-cases queries"
  value       = aws_athena_workgroup.autorater.name
}

output "miner_state_table" {
  description = "DynamoDB cursor/lease table (CURSOR_TABLE for the miner)"
  value       = aws_dynamodb_table.miner_state.name
}

output "dashboard_name" {
  description = "CloudWatch dashboard for the pipeline"
  value       = aws_cloudwatch_dashboard.pipeline.dashboard_name
}
