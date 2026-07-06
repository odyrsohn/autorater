# Two storage accounts, mirroring two AWS services:
#  - state:   Table Storage for the miner cursor + single-runner lease
#             (translation of the DynamoDB miner-state table)
#  - results: ADLS Gen2 (hierarchical namespace) holding the judged-cases
#             JSONL — the filesystem Synapse serverless queries
#             (translation of the S3 results bucket + Glue/Athena)
resource "azurerm_storage_account" "state" {
  name                     = local.state_account_name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  tags                     = local.common_tags
}

resource "azurerm_storage_table" "miner_state" {
  name                 = "minerstate"
  storage_account_name = azurerm_storage_account.state.name
}

resource "azurerm_storage_account" "results" {
  name                     = local.results_account_name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"
  is_hns_enabled           = true # ADLS Gen2 — required by Synapse
  min_tls_version          = "TLS1_2"

  allow_nested_items_to_be_public = false
  tags                            = local.common_tags
}

resource "azurerm_storage_data_lake_gen2_filesystem" "results" {
  name               = "results"
  storage_account_id = azurerm_storage_account.results.id
}

# Cost decay parity with the AWS root (results/* -> IA at 30d).
resource "azurerm_storage_management_policy" "results" {
  storage_account_id = azurerm_storage_account.results.id

  rule {
    name    = "tiering"
    enabled = true
    filters {
      prefix_match = ["results/results/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = 30
      }
    }
  }
}
