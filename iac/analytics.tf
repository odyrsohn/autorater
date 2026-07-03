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
      # Renamed from failure_type — standardized slice-dimension key shared
      # across all three repos (see .plan/standardized-logging.md item 6).
      # Historical rows written under the old column name return NULL here.
      name = "failure_mode"
      type = "string"
    }
    columns {
      name = "safety_categories"
      type = "array<string>"
    }
    columns {
      # Judge-assigned classification, distinct from failure_mode — e.g. a
      # retrieval_failure case can be judge_category="hallucination".
      name = "judge_category"
      type = "string"
    }
    columns {
      name = "lang"
      type = "string"
    }
    columns {
      name = "client_platform"
      type = "string"
    }
    columns {
      name = "client_os_version"
      type = "string"
    }
    columns {
      # Serving model that produced the traffic — distinct from `model`
      # (the judge's own model, see judge-usage-by-model below).
      name = "serving_model"
      type = "string"
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
      SELECT failure_mode, judge_category, count(*) AS cases, avg(score) AS avg_score
      FROM ${local.table}
      WHERE dt >= date_format(date_add('day', -7, current_date), '%Y-%m-%d')
      GROUP BY failure_mode, judge_category
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

    # "Compare the failure rates between the Claude 3.5 Sonnet slice and the
    # open-source fallback model slice" — serving_model is the traffic's
    # own model, NOT the judge model above.
    failure-rate-by-serving-model = <<-SQL
      SELECT dt, serving_model,
             count(*)                                                   AS judged_cases,
             sum(CASE WHEN verdict = 'regression' THEN 1 ELSE 0 END)     AS regressions,
             ROUND(1.0 * sum(CASE WHEN verdict = 'regression' THEN 1 ELSE 0 END) / count(*), 4) AS regression_rate
      FROM ${local.table}
      WHERE dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
        AND serving_model IS NOT NULL
      GROUP BY dt, serving_model
      ORDER BY dt DESC, regression_rate DESC
    SQL

    # "Show me all prompt regressions that occurred exclusively in Spanish."
    regressions-by-language = <<-SQL
      SELECT dt, lang, failure_mode, count(*) AS regressions
      FROM ${local.table}
      WHERE verdict = 'regression'
        AND lang LIKE 'es%'
        AND dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
      GROUP BY dt, lang, failure_mode
      ORDER BY dt DESC, regressions DESC
    SQL

    # "Slice the data to show only requests coming from AAOS 12 or ChromeOS."
    failures-by-client = <<-SQL
      SELECT dt, client_platform, client_os_version, failure_mode, count(*) AS cases
      FROM ${local.table}
      WHERE client_platform IS NOT NULL
        AND dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
      GROUP BY dt, client_platform, client_os_version, failure_mode
      ORDER BY dt DESC, cases DESC
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
# The miner emits one structured "sweep_summary" event per sweep (canonical
# envelope, see .plan/standardized-logging.md); these filters lift the
# cost-control counters into metrics. Filter matches the event NAME
# ($.msg), not a free-text term — event name + these field keys are a
# compatibility contract with worker.py's MiningWorker.report().
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
  pattern        = "{ $.msg = \"sweep_summary\" }"

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
