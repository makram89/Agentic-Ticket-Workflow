# Spec: Azure infrastructure as code (Terraform) for ticketflow

"Apps" = the four Container Apps: api, worker, temporal, temporal-ui.

## Requirements

### Module and state
- R1: A top-level folder `infra/` MUST hold one standalone Terraform root module pinning `required_version >= 1.9`,
  `hashicorp/azurerm` to one major version (4.x) and `hashicorp/random`. `terraform fmt -check -recursive`,
  `terraform init -backend=false` + `terraform validate`, and `tflint` (azurerm ruleset, config in
  `infra/.tflint.hcl`) MUST pass.
- R2: State MUST use `backend "azurerm" {}` (partial config) filled from a `backend.hcl` at init.
  `infra/backend.hcl.example` MUST show every key with placeholders and `use_azuread_auth = true`.

### Resources (nothing beyond this list)
- R3: In one resource group: a random suffix (for the globally unique registry name), a Log Analytics workspace, a
  user-assigned managed identity, an Azure Container Registry (Basic, admin user disabled) with an AcrPull role
  assignment for that identity, a Container Apps environment sending logs to the workspace, and the four apps.
  Names derive from `var.name_prefix`.
- R4: api app: image `<acr login server>/ticketflow:<var.image_tag>` pulled with the managed identity; command
  `uvicorn ticketflow.api.main:app --host 0.0.0.0 --port 8000`; external HTTP ingress on target port 8000, insecure
  connections disabled, no IP restriction; min 1 / max 3 replicas.
- R5: worker app: same image and pull identity; command `python -m ticketflow.worker`; no ingress; min = max = 1
  replica (the fake Helpdesk keeps its idempotency keys in memory).
- R6: temporal app: image `temporalio/temporal:<pinned tag>` (same image as compose, pinned); command
  `server start-dev --ip 0.0.0.0 --headless` (the UI is served by temporal-ui, see R7); internal TCP ingress with
  target and exposed port 7233; min = max = 1 replica. State is in memory and is lost when the revision restarts.
- R7: temporal-ui app: image `temporalio/ui:<pinned tag>`, env `TEMPORAL_ADDRESS=<temporal app name>:7233`, HTTP
  ingress on target port 8080, min = max = 1. Ingress MUST be external with one Allow rule per entry of
  `var.temporal_ui_allowed_cidrs` when that list is non-empty; otherwise internal.
  (A separate app because azurerm's `ingress` block has one `target_port` and no additional port mappings, so the
  start-dev UI port 8233 cannot be exposed next to 7233 on one app.)
- R8: api and worker env: `TEMPORAL_ADDRESS=<temporal app name>:7233`, `TEMPORAL_NAMESPACE=default`,
  `TEMPORAL_TASK_QUEUE=tickets`, `LLM_MODE=var.llm_mode`, `OPENROUTER_MODEL=var.openrouter_model`,
  `HELPDESK_FAILURE_RATE=0.2`, `APPROVAL_TIMEOUT_SECONDS=86400`, `LOG_LEVEL=INFO`, `OTEL_SERVICE_NAME` (`api` /
  `worker`). `OTEL_EXPORTER_OTLP_ENDPOINT` MUST NOT be set.
- R9: When `var.llm_mode = "openrouter"`, the worker (only) MUST get a native Container Apps secret
  `openrouter-api-key` with the value of `var.openrouter_api_key`, exposed as env `OPEN_ROUTER_API_KEY` through
  `secret_name`. In fake mode no app has secrets.

### Inputs, outputs, examples
- R10: Variables, and only these: `location` (string, `"northeurope"`), `name_prefix` (string, `"ticketflow"`),
  `image_tag` (string, required), `llm_mode` (string, `"fake"`), `openrouter_api_key` (string, `""`, sensitive),
  `openrouter_model` (string, `"openai/gpt-4o-mini"`), `temporal_ui_allowed_cidrs` (list(string), `[]`).
  Validation only on: `llm_mode` (one of `fake`, `openrouter`; `openrouter` also requires a non-empty
  `openrouter_api_key`) and `image_tag` (non-empty, not `latest`).
- R11: Outputs: `resource_group_name`, `acr_name`, `acr_login_server`, `api_url` (`https://<api fqdn>`),
  `temporal_ui_url` (`https://<ui fqdn>`, or `null` when internal).
- R12: `infra/terraform.tfvars.example` lists the non-secret variables with defaults/placeholders and says the key
  goes in `TF_VAR_openrouter_api_key`, never in a file.

### Documentation and CI
- R13: `infra/README.md`: short (target under ~100 lines). Prerequisites (Azure CLI, Terraform >= 1.9, a role that
  can create role assignments: Owner, or Contributor + User Access Administrator); a values table with where each goes
  (`backend.hcl`, `terraform.tfvars`, env `ARM_SUBSCRIPTION_ID`, env `TF_VAR_openrouter_api_key`); one-time state
  storage bootstrap; deploy steps (init, targeted apply of the registry, `az acr build`, `terraform apply`, curl smoke
  test); redeploy a new image; teardown; how to run the checks locally; a "Production gaps" section (one line each,
  gap + fix): Temporal persistence (PostgreSQL-backed server or Temporal Cloud, needs app TLS/API-key support),
  single worker / in-memory idempotency, networking (VNet, private endpoints, WAF/Front Door), API auth and rate
  limiting, secrets (Key Vault references + rotation, state storage lockdown; key currently in state), observability
  (App Insights/OTEL, alerts, probes), environments and CI/CD (dev/stage/prod, OIDC plan/apply with approval),
  resilience (zones, backup/DR, ACR tier), supply chain (image scanning/signing, Checkov/Trivy).
- R14: `.github/workflows/infra.yml` runs in `infra/`: `terraform fmt -check -recursive`;
  `terraform init -backend=false` + `terraform validate`; `tflint --init` + `tflint` with pinned tflint and azurerm
  ruleset versions. Triggers: `push` and `pull_request`, each with `paths: [infra/**, .github/workflows/infra.yml]`.
- R15: Root `README.md` Azure section keeps its existing target-architecture table and bullets unchanged (it is the
  production design the brief asks for). Below them, add a short "Described vs. deployed in `infra/`" table (one row
  per README row: Temporal, worker scaling, secrets, auth, LLM, observability — target vs. what `infra/` deploys)
  and a link to `infra/README.md` ("Production gaps"); "next steps" no longer lists IaC as missing.
- R16: `.gitignore` (append only) excludes `.terraform/`, `*.tfstate*`, `*.tfvars` (except `*.tfvars.example`),
  `backend.hcl`, `crash.log`; `infra/.terraform.lock.hcl` is committed.

## Negative requirements
- N1: No secret value, state file, real `backend.hcl` or `terraform.tfvars` MUST be committed.
- N2: The infra checks MUST NOT need Azure credentials or repository secrets, and MUST NOT create, plan against or
  mock Azure resources. (Network only to download Terraform providers and tflint plugins.)
- N3: The infra checks MUST NOT be part of `pytest -q`; `.github/workflows/tests.yml` MUST NOT change.
- N4: `ticketflow/`, `tests/`, `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `.env.example` MUST NOT change.
- N5: Temporal gRPC (7233) MUST NOT be reachable from the internet; the Temporal UI MUST NOT be external without an
  IP allow-list.
- N6: No image MUST use the `latest` tag or no tag.
- N7: The registry admin user MUST NOT be enabled.
- N8: worker, temporal and temporal-ui MUST NOT scale beyond one replica.

## Open questions
- None blocking. Assumed: region default `northeurope`; remote state kept (the OpenRouter key would otherwise sit in
  a local state file).
