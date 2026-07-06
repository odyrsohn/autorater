# Output names mirror iac/aws/outputs.tf concepts.
output "acr_login_server" {
  description = "Image push target (≙ ecr_repositories)"
  value       = azurerm_container_registry.this.login_server
}

output "container_app_environment" {
  description = "Compute environment name (≙ ecs_cluster)"
  value       = azurerm_container_app_environment.this.name
}

output "miner_cron" {
  description = "Cron driving mining sweeps (≙ miner_schedule)"
  value       = var.miner_cron
}

output "cursor_table_endpoint" {
  description = "Table endpoint for the miner cursor/lease (≙ miner_state_table)"
  value       = azurerm_storage_account.state.primary_table_endpoint
}

output "results_account_url" {
  description = "ADLS blob endpoint for judged-case results (≙ results_bucket)"
  value       = azurerm_storage_account.results.primary_blob_endpoint
}

output "synapse_serverless_endpoint" {
  description = "Serverless SQL endpoint querying results (≙ athena_workgroup); views in synapse-queries.sql"
  value       = azurerm_synapse_workspace.this.connectivity_endpoints["sqlOnDemand"]
}

output "key_vault_name" {
  description = "Vault holding the three runtime secrets (see iac/README.md)"
  value       = azurerm_key_vault.this.name
}

output "log_analytics_workspace_id" {
  description = "Workspace with container logs + oncall-slices saved searches"
  value       = azurerm_log_analytics_workspace.this.id
}
