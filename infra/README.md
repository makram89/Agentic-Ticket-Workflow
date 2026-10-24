# Deploying ticketflow to Azure (Terraform)

Deploys the compose setup to Azure Container Apps: `api` (public HTTPS), `worker` (no ingress), `temporal`
(dev server, internal TCP 7233, state in memory) and `temporal-ui` (internal, or external with an IP allow-list).
Images come from an Azure Container Registry pulled with a user-assigned identity; logs go to Log Analytics.
This is a demo deployment, not the production target in the root README; see "Production gaps".

## Prerequisites
- Azure CLI (`az login`), Terraform >= 1.9.
- A role that can create role assignments: Owner, or Contributor + User Access Administrator.

## Values

| Value | Where |
|---|---|
| Subscription id | env `ARM_SUBSCRIPTION_ID` |
| State storage (RG, account, container, key) | `infra/backend.hcl` (copy `backend.hcl.example`) |
| `image_tag`, `location`, `name_prefix`, `llm_mode`, `openrouter_model`, `temporal_ui_allowed_cidrs` | `infra/terraform.tfvars` (copy `terraform.tfvars.example`) |
| OpenRouter key (only for `llm_mode = "openrouter"`) | env `TF_VAR_openrouter_api_key`, never a file |

## One-time state storage bootstrap
```
az group create -n ticketflow-tfstate -l northeurope
az storage account create -n <statestorageaccount> -g ticketflow-tfstate --sku Standard_LRS \
  --allow-blob-public-access false --min-tls-version TLS1_2
az storage container create -n tfstate --account-name <statestorageaccount> --auth-mode login
az role assignment create --role "Storage Blob Data Contributor" --assignee <your-object-id> \
  --scope $(az storage account show -n <statestorageaccount> -g ticketflow-tfstate --query id -o tsv)
```

## Deploy (from the repo root)
```
export ARM_SUBSCRIPTION_ID=<subscription-id>
TAG=$(git rev-parse --short HEAD)
terraform -chdir=infra init -backend-config=backend.hcl
terraform -chdir=infra apply -target=azurerm_container_registry.this -var image_tag=$TAG
az acr build -r $(terraform -chdir=infra output -raw acr_name) -t ticketflow:$TAG .
terraform -chdir=infra apply -var image_tag=$TAG
curl -s -X POST $(terraform -chdir=infra output -raw api_url)/tickets \
  -H 'content-type: application/json' -d '{"ticket_id":"T-smoke","customer_id":"C-100","subject":"Charged twice","body":"Please refund the extra payment."}'
```
If the first apply fails pulling the image (AcrPull not yet propagated), run `terraform apply` again.
If the api/worker/ui cannot reach Temporal, check `TEMPORAL_ADDRESS` and the app logs in Log Analytics first.

## Redeploy a new image
```
TAG=$(git rev-parse --short HEAD)
az acr build -r $(terraform -chdir=infra output -raw acr_name) -t ticketflow:$TAG .
terraform -chdir=infra apply -var image_tag=$TAG
```
Note: a new `temporal` revision wipes Temporal state (in-flight tickets and approvals).

## Teardown
`terraform -chdir=infra destroy -var image_tag=$TAG`, then `az group delete -n ticketflow-tfstate`.

## Checks (offline, no Azure credentials; also in `.github/workflows/infra.yml`)
```
cd infra
terraform fmt -check -recursive
terraform init -backend=false && terraform validate
tflint --init && tflint
```

## Production gaps
- Temporal persistence: in-memory dev server -> PostgreSQL-backed Temporal or Temporal Cloud (needs TLS/API-key support in the app's client).
- Single worker / in-memory idempotency: one worker replica -> real Helpdesk enforcing idempotency keys, then scale the worker (KEDA).
- Networking: public API, no VNet -> VNet-integrated environment, private endpoints (ACR, state), WAF/Front Door.
- API auth and rate limiting: none -> Entra ID (API Management or Container Apps auth) plus rate limits.
- Secrets: OpenRouter key is in Terraform state -> Key Vault references + rotation; lock down the state storage (private endpoint, RBAC only).
- Observability: logs only, default TCP probes -> App Insights/OTEL exporter, alerts, `/health` probes.
- Environments and CI/CD: one manual environment -> dev/stage/prod, OIDC plan/apply in CI with approval.
- Resilience: no zones, no backup -> zone-redundant environment, backup/DR for Temporal DB, Standard/Premium ACR.
- Supply chain: no scanning -> image scanning/signing, Checkov/Trivy on Terraform.
