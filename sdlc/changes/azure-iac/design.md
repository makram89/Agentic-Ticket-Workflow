# Design: Azure infrastructure as code (Terraform) for ticketflow

## Architecture (mirrors docker-compose.yml)

```
Internet --HTTPS--> [ca api :8000, external] --gRPC--> [ca temporal :7233, internal TCP, start-dev, in-memory]
                    [ca worker, no ingress]  --gRPC--^                ^
 allowed IPs --HTTPS--> [ca temporal-ui :8080] ------------------------'
 ACR --(AcrPull via user-assigned identity)--> api, worker
 Container Apps environment --> Log Analytics (stdout JSON logs)
```

| compose service | Azure |
|---|---|
| `temporal` (`server start-dev`, gRPC 7233 + UI 8233) | `temporal` app, same image pinned, `start-dev --headless`, internal TCP 7233; UI moved to `temporal-ui` app |
| `api` | `api` app, external HTTPS ingress -> 8000 |
| `worker` | `worker` app, no ingress |
| `jaeger` | not deployed (tracing off) |

Why a separate UI app: the azurerm `azurerm_container_app` `ingress` block takes a single `target_port` (plus
`exposed_port` for TCP) and has no additional-port-mappings argument
(source: https://github.com/hashicorp/terraform-provider-azurerm/blob/main/website/docs/r/container_app.html.markdown).
Azure's API has `additionalPortMappings`, but using it means adding the azapi provider. Container Apps only route
traffic to a container through ingress, so the start-dev UI on 8233 cannot be reached next to gRPC on 7233 in one
app. A `temporalio/ui` app pointed at `temporal:7233` is the simpler option azurerm supports. Internal TCP ingress
needs no custom VNet; only external TCP does
(source: https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview, "TCP" section).

Trade-offs (stated in both READMEs): Temporal state is in memory and is lost on any temporal revision restart or
replica move (in-flight tickets and approval waits disappear). The API is public with no auth. The OpenRouter key sits
in Terraform state (remote, private storage, Entra ID auth). Production next steps: Temporal on PostgreSQL or
Temporal Cloud (needs client TLS/API key), Key Vault references, VNet, auth.

## Files

```
infra/
  versions.tf               terraform{}: required_version >= 1.9, azurerm ~> 4.x, random ~> 3.x; backend "azurerm" {}
  providers.tf              provider "azurerm" { features {} }   # subscription from ARM_SUBSCRIPTION_ID
  variables.tf              7 variables, 2 validations (spec R10)
  main.tf                   locals (names, pinned Temporal images, common env), random_string, RG, Log Analytics,
                            identity, ACR, AcrPull role assignment, Container Apps environment
  apps.tf                   azurerm_container_app.api / .worker / .temporal / .temporal_ui
  outputs.tf                spec R11
  terraform.tfvars.example
  backend.hcl.example
  .tflint.hcl               plugin "terraform" (preset recommended) + plugin "azurerm" (pinned version)
  .terraform.lock.hcl       committed
  README.md                 deploy guide
.github/workflows/infra.yml
.gitignore                  append terraform entries (file already has local edits; append only)
README.md                   Azure section: keep target table, append described-vs-deployed table + link; next steps line
```

## Public interfaces

Variables (exact): see spec R10. Validations:
- `llm_mode`: `contains(["fake","openrouter"], var.llm_mode) && (var.llm_mode != "openrouter" || var.openrouter_api_key != "")`
  (a cross-variable reference, which needs TF >= 1.9).
- `image_tag`: `var.image_tag != "" && var.image_tag != "latest"`.

Locals (not variables): `temporal_image = "temporalio/temporal:<pinned>"`, `temporal_ui_image =
"temporalio/ui:<pinned>"` (current tags that exist on Docker Hub at implementation time), `tags = { project =
"ticketflow" }`, `temporal_address = "${azurerm_container_app.temporal.name}:7233"` (or the name local).

Resource addresses: `random_string.suffix`, `azurerm_resource_group.this`, `azurerm_log_analytics_workspace.this`,
`azurerm_user_assigned_identity.apps`, `azurerm_container_registry.this`, `azurerm_role_assignment.acr_pull`,
`azurerm_container_app_environment.this`, `azurerm_container_app.{api,worker,temporal,temporal_ui}`.

Sizing defaults: api/worker 0.5 vCPU / 1 Gi; temporal 0.5 vCPU / 1 Gi; temporal-ui 0.25 vCPU / 0.5 Gi. Default TCP
probes (the app has no `/health`). The worker's secret and `OPEN_ROUTER_API_KEY` env are `dynamic` blocks gated on
`var.llm_mode == "openrouter"`. The api and worker apps `depends_on` the AcrPull role assignment.

Deploy commands the guide documents (from repo root, after one-time state bootstrap):
```
terraform -chdir=infra init -backend-config=backend.hcl
terraform -chdir=infra apply -target=azurerm_container_registry.this -var image_tag=$TAG
az acr build -r $(terraform -chdir=infra output -raw acr_name) -t ticketflow:$TAG .
terraform -chdir=infra apply -var image_tag=$TAG
```
Teardown: `terraform -chdir=infra destroy`, then delete the state resource group.

## Test strategy

The user's call: no tests that restate the config, so no `terraform test` and no mocks. Three offline CI checks in
`.github/workflows/infra.yml`, run only when `infra/**` or the workflow file changes:
1. `terraform fmt -check -recursive` (S1)
2. `terraform init -backend=false` + `terraform validate` (S2): types, references, schema of the pinned provider
3. `tflint --init` + `tflint` with the azurerm ruleset (S3): invalid SKUs/regions/names, deprecated arguments,
   unused declarations, missing version pins

Workflow shape: `hashicorp/setup-terraform` (pinned Terraform 1.9+), `terraform-linters/setup-tflint` (pinned
version), `defaults.run.working-directory: infra`, `GITHUB_TOKEN` passed to `tflint --init` (plugin download rate
limit), `permissions: contents: read`. No Azure login, no `secrets.*` otherwise.

SDLC test stage: the test agent writes the "test surface" of this change: `.github/workflows/infra.yml` and
`infra/.tflint.hcl`. There is no meaningful red phase (fmt/validate pass on an empty directory), so the gate is
"the commands below pass, plus review of S4-S6". S7 is the user's optional manual check. Nothing is added to
`tests/` or pytest.

## Test command

`terraform -chdir=infra fmt -check -recursive && terraform -chdir=infra init -backend=false -input=false && terraform -chdir=infra validate && (cd infra && tflint --init && tflint)`
plus the unchanged repo `TEST_CMD`: `pytest -q`.
If the coder sandbox has no `terraform`/`tflint` or no registry access, these run only in GitHub Actions.

## Constraints
- Terraform >= 1.9, azurerm 4.x (use 4.x argument names), subscription via `ARM_SUBSCRIPTION_ID`.
- No resources beyond spec R3; no Key Vault, PostgreSQL, VNet, App Insights.
- Worker, temporal and temporal-ui fixed at 1 replica; api 1 to 3.
- Guide short; no copy of the root README architecture beyond a few lines.

## Risks that can only be checked by a real deploy (S7)
| Risk | Mitigation |
|---|---|
| Temporal state lost on revision restart, platform maintenance or crash; in-flight tickets/approvals vanish | accepted trade-off, documented; min = max = 1 avoids scale events; next step PostgreSQL or Temporal Cloud |
| start-dev listens where ingress can't reach it, or gRPC over internal TCP ingress misbehaves | `--ip 0.0.0.0` as in compose; if it fails, check the app's logs in Log Analytics |
| `temporalio/ui` can't reach `<temporal app name>:7233` (in-environment name resolution for TCP apps) | same address the api/worker use; the guide lists `TEMPORAL_ADDRESS` as the first thing to check |
| AcrPull role assignment not yet propagated at first app creation: image pull fails | `depends_on`; guide: re-run `terraform apply` |
| API starts before Temporal is reachable | the app already retries `connect()` (10 attempts); Container Apps restarts the container if it exits |
| Container Apps or Log Analytics capacity/offer restrictions in the region | `location` variable |
| Pinned tflint ruleset or Temporal image tags go stale | pinned on purpose; bump them by hand |
