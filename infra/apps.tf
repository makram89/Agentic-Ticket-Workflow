resource "azurerm_container_app" "api" {
  name                         = "${var.name_prefix}-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  ingress {
    external_enabled           = true
    target_port                = 8000
    transport                  = "http"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name    = "api"
      image   = local.app_image
      command = ["uvicorn", "ticketflow.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
      cpu     = 0.5
      memory  = "1Gi"

      dynamic "env" {
        for_each = merge(local.common_env, { OTEL_SERVICE_NAME = "api" })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull]
}

resource "azurerm_container_app" "worker" {
  name                         = "${var.name_prefix}-worker"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = var.llm_mode == "openrouter" ? [1] : []
    content {
      name  = "openrouter-api-key"
      value = var.openrouter_api_key
    }
  }

  template {
    # The fake Helpdesk keeps idempotency keys in memory: exactly one replica.
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "worker"
      image   = local.app_image
      command = ["python", "-m", "ticketflow.worker"]
      cpu     = 0.5
      memory  = "1Gi"

      dynamic "env" {
        for_each = merge(local.common_env, { OTEL_SERVICE_NAME = "worker" })
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.llm_mode == "openrouter" ? [1] : []
        content {
          name        = "OPEN_ROUTER_API_KEY"
          secret_name = "openrouter-api-key"
        }
      }
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull]
}

resource "azurerm_container_app" "temporal" {
  name                         = local.temporal_app_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags

  # Internal TCP only: gRPC is never reachable from the internet.
  ingress {
    external_enabled = false
    target_port      = 7233
    exposed_port     = 7233
    transport        = "tcp"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # Dev server, state in memory (lost on revision restart): exactly one replica.
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "temporal"
      image   = local.temporal_image
      command = ["temporal", "server", "start-dev", "--ip", "0.0.0.0", "--headless"]
      cpu     = 0.5
      memory  = "1Gi"
    }
  }
}

resource "azurerm_container_app" "temporal_ui" {
  name                         = "${var.name_prefix}-temporal-ui"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags

  ingress {
    external_enabled = length(var.temporal_ui_allowed_cidrs) > 0
    target_port      = 8080
    transport        = "http"

    dynamic "ip_security_restriction" {
      for_each = var.temporal_ui_allowed_cidrs
      content {
        name             = "allow-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
    }

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "temporal-ui"
      image  = local.temporal_ui_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "TEMPORAL_ADDRESS"
        value = local.temporal_address
      }
    }
  }
}
