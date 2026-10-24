output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "acr_name" {
  value = azurerm_container_registry.this.name
}

output "acr_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "api_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "temporal_ui_url" {
  value = length(var.temporal_ui_allowed_cidrs) > 0 ? "https://${azurerm_container_app.temporal_ui.ingress[0].fqdn}" : null
}
