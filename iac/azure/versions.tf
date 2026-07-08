terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state — storage account/container bootstrapped out-of-band.
  # Blob leases provide locking natively (≙ the S3 backend's DynamoDB table).
  backend "azurerm" {
    resource_group_name  = "mlops-terraform-state"
    storage_account_name = "mlopstfstate"
    container_name       = "tfstate"
    key                  = "autorater.tfstate"
  }
}
