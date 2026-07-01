variable "aws_region" {
  description = "Deployment region"
  type        = string
  default     = "eu-central-1"
}

variable "app_name" {
  description = "app:name default tag"
  type        = string
  default     = "autorater"
}

variable "project_name" {
  description = "app:projectName default tag"
  type        = string
  default     = "eval-mining-autorater"
}

variable "component" {
  description = "app:component default tag"
  type        = string
  default     = "evaluation-pipeline"
}

variable "team_name" {
  description = "app:teamName default tag"
  type        = string
  default     = "mlops-platform"
}

variable "env" {
  description = "app:env default tag (dev|staging|prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "env must be dev, staging or prod."
  }
}

variable "vpc_subnet_ids" {
  description = "Private subnets for Fargate tasks"
  type        = list(string)
}

variable "vpc_security_group_ids" {
  description = "Security groups for Fargate tasks"
  type        = list(string)
}

variable "miner_schedule" {
  description = "EventBridge cron/rate expression triggering the mining sweep"
  type        = string
  default     = "rate(15 minutes)"
}

variable "data_lake_bucket" {
  description = "Ingestion data lake the miner polls (from the ingestion stack)"
  type        = string
}
