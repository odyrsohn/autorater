# Variable names mirror iac/aws/variables.tf where the concept exists on
# both clouds; Azure-only inputs are grouped at the bottom.
variable "location" {
  description = "Azure region (translation of aws_region)"
  type        = string
  default     = "eastus"
}

variable "app_name" {
  description = "app:name cost tag"
  type        = string
  default     = "autorater"
}

variable "project_name" {
  description = "app:projectName cost tag"
  type        = string
  default     = "eval-mining-autorater"
}

variable "component" {
  description = "app:component cost tag"
  type        = string
  default     = "evaluation-pipeline"
}

variable "team_name" {
  description = "app:teamName cost tag"
  type        = string
  default     = "mlops-platform"
}

variable "env" {
  description = "app:env cost tag (dev|staging|prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "env must be dev, staging or prod."
  }
}

variable "judge_model" {
  description = "OpenRouter model id for the LLM-as-Judge (identical to the aws root)"
  type        = string
  default     = "google/gemini-2.5-flash"
}

variable "miner_cron" {
  description = "Container Apps Job cron for mining sweeps (translation of miner_schedule; EventBridge rate(15 minutes) ≙ */15 * * * *)"
  type        = string
  default     = "*/15 * * * *"
}

# Cross-stack input: the ingestion pipeline's data lake the miner polls
# (translation of data_lake_bucket).
variable "data_lake_account_url" {
  description = "Blob endpoint of the ingestion data lake storage account"
  type        = string
}

variable "data_lake_container" {
  description = "Container holding tenants/<id>/... records"
  type        = string
  default     = "data-lake"
}
