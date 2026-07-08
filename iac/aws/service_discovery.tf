# Private DNS so the miner can reach the alerting webhook by name
# (alerting.<app_name>.local) regardless of which task/IP is currently
# running behind the scale-to-zero service (ecs.tf, step_functions.tf).
resource "aws_service_discovery_private_dns_namespace" "internal" {
  name = "${var.app_name}.local"
  vpc  = var.vpc_id
}

resource "aws_service_discovery_service" "alerting" {
  name = "alerting"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.internal.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}
