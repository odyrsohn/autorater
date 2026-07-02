# Query surface for judged cases: the miner flushes JSONL to a
# date-partitioned results prefix; Glue partition projection makes it
# queryable in Athena with zero crawlers; a CloudWatch dashboard shows the
# pipeline's operational and cost-control metrics.
resource "aws_s3_bucket" "results" {
  bucket = "${var.app_name}-${var.env}-results-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket                  = aws_s3_bucket.results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    id     = "tiering"
    status = "Enabled"

    filter {
      prefix = "results/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }

  rule {
    id     = "athena-output-hygiene"
    status = "Enabled"

    filter {
      prefix = "athena-output/"
    }

    expiration {
      days = 7
    }
  }
}

resource "aws_iam_role_policy" "miner_results" {
  name = "miner-results"
  role = aws_iam_role.miner_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.results.arn}/results/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey"]
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "s3.${var.aws_region}.amazonaws.com" }
        }
      },
    ]
  })
}

# --- Glue catalog with partition projection (no crawler, no MSCK) ----------
resource "aws_glue_catalog_database" "autorater" {
  name = "${var.app_name}_${var.env}"
}

resource "aws_glue_catalog_table" "judged_cases" {
  name          = "judged_cases"
  database_name = aws_glue_catalog_database.autorater.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                      = "TRUE"
    "classification"              = "json"
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "2026-01-01,NOW"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "storage.location.template"   = "s3://${aws_s3_bucket.results.bucket}/results/dt=$${dt}/"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.results.bucket}/results/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "case_id"
      type = "string"
    }
    columns {
      name = "tenant_id"
      type = "string"
    }
    columns {
      name = "failure_type"
      type = "string"
    }
    columns {
      name = "safety_categories"
      type = "array<string>"
    }
    columns {
      name = "score"
      type = "int"
    }
    columns {
      name = "verdict"
      type = "string"
    }
    columns {
      name = "rationale"
      type = "string"
    }
    columns {
      name = "model"
      type = "string"
    }
    columns {
      name = "window_failure_rate"
      type = "double"
    }
    columns {
      name = "alerted"
      type = "boolean"
    }
    columns {
      name = "sweep_id"
      type = "string"
    }
    columns {
      name = "ts"
      type = "string"
    }
  }
}

# --- Athena workgroup + canned queries --------------------------------------
resource "aws_athena_workgroup" "autorater" {
  name = "${var.app_name}-${var.env}"

  configuration {
    enforce_workgroup_configuration = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.results.bucket}/athena-output/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

locals {
  table = "${aws_glue_catalog_database.autorater.name}.judged_cases"

  named_queries = {
    regression-rate-by-day-tenant = <<-SQL
      SELECT dt, tenant_id,
             count(*)                                          AS judged_cases,
             avg(score)                                        AS avg_score,
             sum(CASE WHEN verdict = 'regression' THEN 1 ELSE 0 END) AS regressions,
             sum(CASE WHEN alerted THEN 1 ELSE 0 END)          AS alerts
      FROM ${local.table}
      WHERE dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
      GROUP BY dt, tenant_id
      ORDER BY dt DESC, regressions DESC
    SQL

    top-failure-types = <<-SQL
      SELECT failure_type, count(*) AS cases, avg(score) AS avg_score
      FROM ${local.table}
      WHERE dt >= date_format(date_add('day', -7, current_date), '%Y-%m-%d')
      GROUP BY failure_type
      ORDER BY cases DESC
    SQL

    safety-category-volumes = <<-SQL
      SELECT dt, cat AS safety_category, count(*) AS findings
      FROM ${local.table}
      CROSS JOIN UNNEST(safety_categories) AS t(cat)
      WHERE dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
      GROUP BY dt, cat
      ORDER BY dt DESC, findings DESC
    SQL

    judge-usage-by-model = <<-SQL
      SELECT dt, model, count(*) AS judged_cases, avg(score) AS avg_score
      FROM ${local.table}
      WHERE dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
      GROUP BY dt, model
      ORDER BY dt DESC
    SQL
  }
}

resource "aws_athena_named_query" "canned" {
  for_each = local.named_queries

  name      = "${var.app_name}-${var.env}-${each.key}"
  workgroup = aws_athena_workgroup.autorater.id
  database  = aws_glue_catalog_database.autorater.name
  query     = each.value
}

# --- CloudWatch metrics + dashboard -----------------------------------------
# The miner prints one pure-JSON stats line per sweep; these filters lift the
# cost-control counters into metrics.
locals {
  miner_metrics = {
    JudgeCalls        = "$.judge_calls"
    JudgeFailures     = "$.judge_failures"
    SuppressedByDedup = "$.suppressed_by_dedup"
    SafetyFlags       = "$.safety_flags"
  }
}

resource "aws_cloudwatch_log_metric_filter" "miner" {
  for_each = local.miner_metrics

  name           = "${var.app_name}-${var.env}-${each.key}"
  log_group_name = aws_cloudwatch_log_group.miner.name
  pattern        = "{ $.metric = \"miner_stats\" }"

  metric_transformation {
    name      = each.key
    namespace = "Autorater/${var.env}"
    value     = each.value
  }
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${var.app_name}-${var.env}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Judge calls vs dedup suppressions (cost gate)"
          region = var.aws_region
          stat   = "Sum"
          period = 900
          metrics = [
            ["Autorater/${var.env}", "JudgeCalls"],
            ["Autorater/${var.env}", "SuppressedByDedup"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Regression alerts dispatched"
          region = var.aws_region
          stat   = "Sum"
          period = 900
          metrics = [
            ["Autorater/${var.env}", "AlertsDispatched"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Safety flags"
          region = var.aws_region
          stat   = "Sum"
          period = 900
          metrics = [
            ["Autorater/${var.env}", "SafetyFlags"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Judge failures (fallback verdicts)"
          region = var.aws_region
          stat   = "Sum"
          period = 900
          metrics = [
            ["Autorater/${var.env}", "JudgeFailures"],
          ]
        }
      },
    ]
  })
}
