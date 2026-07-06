# alert-storm alarm translation: a scheduled-query (log) alert counting
# alert_dispatched events in Log Analytics — the CloudWatch metric-filter +
# alarm pair collapses into one KQL-driven rule on Azure.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "alert_storm" {
  name                = "${var.app_name}-${var.env}-alert-storm"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  description         = "Regression alert volume abnormally high — likely a bad model/prompt deploy"
  severity            = 2
  enabled             = true
  tags                = local.common_tags

  scopes                  = [azurerm_log_analytics_workspace.this.id]
  evaluation_frequency    = "PT15M"
  window_duration         = "PT15M"
  auto_mitigation_enabled = true

  criteria {
    query = <<-KQL
      ContainerAppConsoleLogs_CL
      | extend e = parse_json(Log_s)
      | where e.msg == "alert_dispatched"
    KQL

    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 10

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
}
