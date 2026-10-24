locals {
  temporal_image    = "temporalio/temporal:1.4.1"
  temporal_ui_image = "temporalio/ui:2.36.0"
  tags              = { project = "ticketflow" }

  temporal_app_name = "${var.name_prefix}-temporal"
  temporal_address  = "${local.temporal_app_name}:7233"
  app_image         = "${azurerm_container_registry.this.login_server}/ticketflow:${var.image_tag}"

  common_env = {
    TEMPORAL_ADDRESS         = local.temporal_address
    TEMPORAL_NAMESPACE       = "default"
    TEMPORAL_TASK_QUEUE      = "tickets"
    LLM_MODE                 = var.llm_mode
    OPENROUTER_MODEL         = var.openrouter_model
    HELPDESK_FAILURE_RATE    = "0.2"
    APPROVAL_TIMEOUT_SECONDS = "86400"
    LOG_LEVEL                = "INFO"
  }
}

resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

resource "azurerm_resource_group" "this" {
  name     = "${var.name_prefix}-rg"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name_prefix}-logs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "apps" {
  name                = "${var.name_prefix}-apps"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_container_registry" "this" {
  name                = "${replace(var.name_prefix, "-", "")}${random_string.suffix.result}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.name_prefix}-env"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = local.tags
}
