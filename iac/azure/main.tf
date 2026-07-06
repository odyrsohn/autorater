provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
}

data "azurerm_client_config" "current" {}

# Same five cost tags as the AWS root; azurerm has no provider default_tags
# so they're merged per resource (see docs/cloud-portability.md).
locals {
  common_tags = {
    "app:name"        = var.app_name
    "app:projectName" = var.project_name
    "app:component"   = var.component
    "app:teamName"    = var.team_name
    "app:env"         = var.env
  }

  # Storage-account names: 3-24 lowercase alphanumerics, globally unique.
  state_account_name   = replace("${var.app_name}${var.env}state", "-", "")
  results_account_name = replace("${var.app_name}${var.env}results", "-", "")
}

resource "azurerm_resource_group" "this" {
  name     = "${var.app_name}-${var.env}"
  location = var.location
  tags     = local.common_tags
}
