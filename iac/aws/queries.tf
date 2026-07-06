# Saved CloudWatch Logs Insights queries — the on-call slicing menu across
# the miner and alerting log groups. See .plan/standardized-logging.md and
# docs/sdd/specs/SPEC-001..005 for the event/field contract these depend on.
# (Athena named_query resources in analytics.tf answer the same questions
# over history; these answer them over the last hours, live.)
locals {
  autorater_log_groups = [aws_cloudwatch_log_group.miner.name, aws_cloudwatch_log_group.alerting.name]

  autorater_saved_queries = {
    # "Show me only the logs for Tenant A, because they are the only ones
    # complaining about an outage."
    "by-tenant" = <<-QUERY
      fields @timestamp, service, msg, failure_mode, score, serving_model, lang, client_platform
      | filter tenant_id = "TENANT_ID_HERE"
      | sort @timestamp desc
      | limit 200
    QUERY

    # "Filter the data to show only 'hallucination' or 'ASR/TTS degradation'
    # classifications." — hallucination is a judge_category; ASR/TTS
    # degradation is an upstream failure_mode passthrough — both filterable
    # in one query.
    "by-failure-mode" = <<-QUERY
      fields @timestamp, tenant_id, msg, failure_mode, judge_category, score
      | filter msg = "case_judged"
        and (failure_mode in ["asr_degradation", "tts_degradation"] or judge_category = "hallucination")
      | stats count() as cases by coalesce(judge_category, failure_mode), bin(15m)
    QUERY

    # "Show me all prompt regressions that occurred exclusively in Spanish."
    "by-language" = <<-QUERY
      fields @timestamp, tenant_id, case_id, failure_mode, score
      | filter msg = "case_judged" and lang like /^es/ and verdict = "regression"
      | sort @timestamp desc
    QUERY

    # "Show me all requests coming from AAOS 12 or ChromeOS."
    "by-client" = <<-QUERY
      fields @timestamp, tenant_id, failure_mode, client_platform, client_os_version
      | filter client_platform = "aaos" and client_os_version like /^12/
      | stats count() as cases by failure_mode
    QUERY

    # "Compare the failure rates between the Claude 3.5 Sonnet slice and the
    # open-source fallback model slice."
    "by-model" = <<-QUERY
      fields serving_model, verdict
      | filter msg = "case_judged"
      | stats count() as cases, sum(verdict = "regression") as regressions by serving_model
    QUERY
  }
}

resource "aws_cloudwatch_query_definition" "oncall" {
  for_each = local.autorater_saved_queries

  name            = "${var.app_name}-${var.env}/${each.key}"
  log_group_names = local.autorater_log_groups
  query_string    = each.value
}
