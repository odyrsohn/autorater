# ECS + EventBridge translation: Container Apps environment with an
# always-on alerting app and a CRON-TRIGGERED Container Apps Job for the
# miner — a cleaner fit than EventBridge→RunTask (the cron trigger is a
# property of the job itself, no separate rule/target/IAM pass-role chain).
resource "azurerm_container_registry" "this" {
  name                = replace("${var.app_name}${var.env}acr", "-", "")
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.common_tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.app_name}-${var.env}-logs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.common_tags
}

# X-Ray translation.
resource "azurerm_application_insights" "this" {
  name                = "${var.app_name}-${var.env}-appinsights"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "other"
  sampling_percentage = 10 # ≙ the 10% X-Ray sampling rule
  tags                = local.common_tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.app_name}-${var.env}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = local.common_tags
}

# --- identities + RBAC --------------------------------------------------------
resource "azurerm_user_assigned_identity" "miner" {
  name                = "${var.app_name}-${var.env}-miner"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_user_assigned_identity" "alerting" {
  name                = "${var.app_name}-${var.env}-alerting"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

resource "azurerm_role_assignment" "miner_acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.miner.principal_id
}

resource "azurerm_role_assignment" "alerting_acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.alerting.principal_id
}

# Cursor + lease table (≙ the DynamoDB IAM policy).
resource "azurerm_role_assignment" "miner_table" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.miner.principal_id
}

# Results filesystem writes (≙ s3:PutObject on results/*).
resource "azurerm_role_assignment" "miner_results_writer" {
  scope                = azurerm_storage_account.results.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.miner.principal_id
}

resource "azurerm_role_assignment" "miner_kv_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.miner.principal_id
}

resource "azurerm_role_assignment" "alerting_kv_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.alerting.principal_id
}

# --- alerting: always-on Container App ----------------------------------------
resource "azurerm_container_app" "alerting" {
  name                         = "alerting"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.common_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.alerting.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.alerting.id
  }

  secret {
    name                = "slack-webhook-url"
    key_vault_secret_id = azurerm_key_vault_secret.secret["SLACK-WEBHOOK-URL"].id
    identity            = azurerm_user_assigned_identity.alerting.id
  }

  secret {
    name                = "pagerduty-routing-key"
    key_vault_secret_id = azurerm_key_vault_secret.secret["PAGERDUTY-ROUTING-KEY"].id
    identity            = azurerm_user_assigned_identity.alerting.id
  }

  ingress {
    external_enabled = false # only the miner calls it, inside the environment
    target_port      = 8070
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 2 # ≙ ECS desired_count = 2
    max_replicas = 3

    container {
      name   = "alerting"
      image  = "${azurerm_container_registry.this.login_server}/autorater-alerting:latest"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "APP_ENV"
        value = var.env
      }
      env {
        name        = "SLACK_WEBHOOK_URL"
        secret_name = "slack-webhook-url"
      }
      env {
        name        = "PAGERDUTY_ROUTING_KEY"
        secret_name = "pagerduty-routing-key"
      }
    }
  }
}

# --- miner: cron-triggered Container Apps Job ----------------------------------
resource "azurerm_container_app_job" "miner" {
  name                         = "miner"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  tags                         = local.common_tags

  replica_timeout_in_seconds = 900 # matches the DynamoDB/Table lease TTL
  replica_retry_limit        = 1

  schedule_trigger_config {
    cron_expression          = var.miner_cron
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.miner.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.miner.id
  }

  secret {
    name                = "openrouter-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.secret["OPENROUTER-API-KEY"].id
    identity            = azurerm_user_assigned_identity.miner.id
  }

  template {
    container {
      name   = "miner"
      image  = "${azurerm_container_registry.this.login_server}/autorater-miner:latest"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "CLOUD_PROVIDER"
        value = "azure"
      }
      env {
        name  = "APP_ENV"
        value = var.env
      }
      env {
        name  = "JUDGE_MODEL"
        value = var.judge_model
      }
      env {
        name  = "ALERT_WEBHOOK_URL"
        value = "http://alerting/v1/alerts" # in-environment DNS
      }
      env {
        name  = "DATA_LAKE_ACCOUNT_URL"
        value = var.data_lake_account_url
      }
      env {
        name  = "DATA_LAKE_CONTAINER"
        value = var.data_lake_container
      }
      env {
        name  = "CURSOR_TABLE_ENDPOINT"
        value = azurerm_storage_account.state.primary_table_endpoint
      }
      env {
        name  = "CURSOR_TABLE_NAME"
        value = azurerm_storage_table.miner_state.name
      }
      env {
        name  = "RESULTS_ACCOUNT_URL"
        value = azurerm_storage_account.results.primary_blob_endpoint
      }
      env {
        name  = "RESULTS_CONTAINER"
        value = azurerm_storage_data_lake_gen2_filesystem.results.name
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.miner.client_id
      }
      env {
        name        = "OPENROUTER_API_KEY"
        secret_name = "openrouter-api-key"
      }
    }
  }
}
