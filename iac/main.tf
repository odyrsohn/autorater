# FinOps mandate: every resource inherits the cost-allocation tag set via
# provider default_tags. Do NOT tag resources individually with these keys.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "app:name"        = var.app_name
      "app:projectName" = var.project_name
      "app:component"   = var.component
      "app:teamName"    = var.team_name
      "app:env"         = var.env
    }
  }
}

data "aws_caller_identity" "current" {}
