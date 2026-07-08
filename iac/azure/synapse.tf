# Glue + Athena translation: Synapse workspace with the BUILT-IN serverless
# SQL pool — pay-per-query over data-lake files, like Athena. Athena named
# queries have no first-class azurerm resource; the OPENROWSET translations
# of all seven canned queries live in iac/azure/synapse-queries.sql
# (partition projection ≙ filepath() functions over the dt=*/ layout).
resource "random_password" "synapse_sql_admin" {
  length  = 24
  special = true
}

# The generated admin password is parked in Key Vault (never in state-adjacent
# docs); day-to-day access should use Entra ID instead of SQL auth.
resource "azurerm_key_vault_secret" "synapse_sql_admin" {
  name         = "SYNAPSE-SQL-ADMIN-PASSWORD"
  key_vault_id = azurerm_key_vault.this.id
  value        = random_password.synapse_sql_admin.result

  depends_on = [azurerm_role_assignment.deployer_kv_secrets]
}

resource "azurerm_synapse_workspace" "this" {
  name                                 = "${var.app_name}-${var.env}-synapse"
  resource_group_name                  = azurerm_resource_group.this.name
  location                             = azurerm_resource_group.this.location
  storage_data_lake_gen2_filesystem_id = azurerm_storage_data_lake_gen2_filesystem.results.id

  sql_administrator_login          = "sqladmin"
  sql_administrator_login_password = random_password.synapse_sql_admin.result

  identity {
    type = "SystemAssigned"
  }

  tags = local.common_tags
}

# Serverless SQL reads the results filesystem through the workspace identity.
resource "azurerm_role_assignment" "synapse_results_reader" {
  scope                = azurerm_storage_account.results.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_synapse_workspace.this.identity[0].principal_id
}

# ≙ the Athena workgroup being reachable from AWS services.
resource "azurerm_synapse_firewall_rule" "allow_azure_services" {
  name                 = "AllowAllWindowsAzureIps"
  synapse_workspace_id = azurerm_synapse_workspace.this.id
  start_ip_address     = "0.0.0.0"
  end_ip_address       = "0.0.0.0"
}
