variable "location" {
  description = "Azure region."
  type        = string
  default     = "northeurope"
}

variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "ticketflow"
}

variable "image_tag" {
  description = "Tag of the ticketflow image in the registry (not latest)."
  type        = string

  validation {
    condition     = var.image_tag != "" && var.image_tag != "latest"
    error_message = "image_tag must be non-empty and not \"latest\"."
  }
}

variable "llm_mode" {
  description = "LLM mode: fake or openrouter."
  type        = string
  default     = "fake"

  validation {
    condition     = contains(["fake", "openrouter"], var.llm_mode) && (var.llm_mode != "openrouter" || var.openrouter_api_key != "")
    error_message = "llm_mode must be fake or openrouter; openrouter requires openrouter_api_key."
  }
}

variable "openrouter_api_key" {
  description = "OpenRouter API key (set via TF_VAR_openrouter_api_key)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "openrouter_model" {
  description = "OpenRouter model id."
  type        = string
  default     = "openai/gpt-4o-mini"
}

variable "temporal_ui_allowed_cidrs" {
  description = "CIDRs allowed to reach the Temporal UI; empty keeps it internal."
  type        = list(string)
  default     = []
}
