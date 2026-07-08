# Saved Logs Insights queries translation: Log Analytics saved searches
# (KQL over ContainerAppConsoleLogs_CL, parsing the canonical JSON
# envelope). Same five on-call slices as iac/aws/queries.tf; Synapse
# (synapse-queries.sql) answers the same questions over history.
locals {
  envelope = "ContainerAppConsoleLogs_CL | extend e = parse_json(Log_s)"

  saved_searches = {
    "by-tenant" = <<-KQL
      ${local.envelope}
      | where e.tenant_id == "TENANT_ID_HERE"
      | project TimeGenerated, service = e.service, msg = e.msg, failure_mode = e.failure_mode,
                score = e.score, serving_model = e.serving_model, lang = e.lang,
                client_platform = e.client_platform
      | order by TimeGenerated desc
      | take 200
    KQL

    "by-failure-mode" = <<-KQL
      ${local.envelope}
      | where e.msg == "case_judged"
              and (e.failure_mode in ("asr_degradation", "tts_degradation")
                   or e.judge_category == "hallucination")
      | summarize cases = count() by classification = coalesce(tostring(e.judge_category), tostring(e.failure_mode)), bin(TimeGenerated, 15m)
    KQL

    "by-language" = <<-KQL
      ${local.envelope}
      | where e.msg == "case_judged" and tostring(e.lang) startswith "es"
              and e.verdict == "regression"
      | project TimeGenerated, tenant_id = e.tenant_id, case_id = e.case_id,
                failure_mode = e.failure_mode, score = e.score
      | order by TimeGenerated desc
    KQL

    "by-client" = <<-KQL
      ${local.envelope}
      | where e.client_platform == "aaos" and tostring(e.client_os_version) startswith "12"
      | summarize cases = count() by failure_mode = tostring(e.failure_mode)
    KQL

    "by-model" = <<-KQL
      ${local.envelope}
      | where e.msg == "case_judged"
      | summarize cases = count(), regressions = countif(e.verdict == "regression")
                by serving_model = tostring(e.serving_model)
    KQL
  }
}

resource "azurerm_log_analytics_saved_search" "oncall" {
  for_each = local.saved_searches

  name                       = "${var.app_name}-${var.env}-${each.key}"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  category                   = "oncall-slices"
  display_name               = "${var.app_name}-${var.env}/${each.key}"
  query                      = each.value
}
