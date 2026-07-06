# SSM SecureString translation: Key Vault secrets. Terraform owns
# existence/naming with placeholders and ignores value changes; real values
# are set manually (see iac/README.md). KV secret names disallow
# underscores — the container env var names are unchanged.
locals {
  secrets = [
    "OPENROUTER-API-KEY",    # miner: LLM-as-Judge via OpenRouter
    "SLACK-WEBHOOK-URL",     # alerting: Slack incoming webhook
    "PAGERDUTY-ROUTING-KEY", # alerting: PagerDuty Events v2 routing key
  ]
}

resource "azurerm_key_vault" "this" {
  name                = replace("${var.app_name}-${var.env}-kv", "-", "")
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  purge_protection_enabled   = false
  rbac_authorization_enabled = true
  tags                       = local.common_tags
}

resource "azurerm_role_assignment" "deployer_kv_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_key_vault_secret" "secret" {
  for_each = toset(local.secrets)

  name         = each.key
  key_vault_id = azurerm_key_vault.this.id
  value        = "CHANGE_ME"

  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [azurerm_role_assignment.deployer_kv_secrets]
}
