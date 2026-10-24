# Intent: Azure infrastructure as code (Terraform) for ticketflow

## Problem
The assignment lists "Terraform or Bicep for the Azure deployment you describe" as an extra. The README describes an
Azure deployment in prose, but nothing in the repo can create it, and "next steps" still lists IaC as missing.

## Goals
- G1: A separate top-level folder `infra/` with a small, complete Terraform root module that deploys the service to
  Azure **the same way `docker-compose.yml` runs it locally**: api and worker from one image with two commands, and
  Temporal as a single `temporalio/temporal` container running `server start-dev` (in-memory, no database), all on
  Azure Container Apps. Images in Azure Container Registry, logs in Log Analytics.
- G2: Temporal UI reachable from the internet only for listed IP ranges; internal-only otherwise.
- G3: Remote state (azurerm backend, partial config), a handful of variables, outputs, example files with no secrets.
- G4: A short deploy guide `infra/README.md`: prerequisites, values and where they go, steps (build image, apply),
  teardown.
- G5: Offline CI checks only (fmt, validate, tflint) in a separate GitHub Actions workflow, run only when `infra/`
  changes, needing no Azure credentials.
- G6: The root README's Azure section points to `infra/` and states the trade-offs of this deployment.

## Non-goals
- Changing `ticketflow/`, `tests/`, `Dockerfile`, `docker-compose.yml` (for example TLS/API key for Temporal Cloud).
- Persistent Temporal state: no PostgreSQL, no Temporal Cloud. State is lost when the Temporal revision restarts;
  documented as the first production next step.
- Key Vault, private networking/VNet, API Management, auth in front of the API, Azure OpenAI.
- Jaeger / tracing / Application Insights in Azure (tracing off; stdout JSON logs go to Log Analytics).
- A deploy workflow in GitHub Actions or OIDC setup (deploy is a few manual CLI commands).
- `terraform test` or any test that creates, plans against, or mocks Azure resources; adding checks to `pytest -q`.

## Success criteria
- SC1: The infra workflow passes in GitHub Actions without credentials: fmt -check, init -backend=false + validate, tflint.
- SC2: `pytest -q` and `.github/workflows/tests.yml` are unchanged and still pass.
- SC3: Following `infra/README.md` alone, a person with an Azure subscription can deploy, process example ticket T-1
  in fake mode through the public API URL, open the Temporal UI from an allowed IP, and tear everything down (manual).
- SC4: No secret value is committed. The only secret is the optional OpenRouter key, passed as
  `TF_VAR_openrouter_api_key` and given to the worker only, as a Container Apps secret.
