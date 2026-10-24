# Scenarios: Azure infrastructure as code (Terraform) for ticketflow
Each scenario covers requirement(s); every requirement needs at least one scenario.

Verification: **CI** = step in `.github/workflows/infra.yml` (not in `pytest -q`); **M** = gate review checklist;
**D** = optional manual check after a real deploy, done by the user.

## S1 (covers R1) [CI]: Formatting is canonical
- GIVEN the files in `infra/`
- WHEN `terraform fmt -check -recursive` runs in `infra/`
- THEN it exits 0 and lists no files

## S2 (covers R1, R2, R3-R11) [CI]: Module initialises and validates offline
- GIVEN a clean checkout, Terraform >= 1.9, no Azure credentials
- WHEN `terraform init -backend=false` then `terraform validate` run in `infra/`
- THEN both exit 0 (all resources, variables, validations and outputs are valid against the pinned azurerm schema)

## S3 (covers R1) [CI]: tflint with the azurerm ruleset is clean
- GIVEN `infra/.tflint.hcl` enabling the pinned azurerm ruleset
- WHEN `tflint --init` then `tflint` run in `infra/`
- THEN tflint exits 0 with no issues

## S4 (covers R14, N2, N3) [M + CI run]: Infra workflow is path-filtered and credential-free
- GIVEN `.github/workflows/infra.yml`
- WHEN it is reviewed, and a push touches only `ticketflow/` and then another touches `infra/`
- THEN it triggers only on `infra/**` or its own file (push and pull_request), runs S1-S3, uses no `secrets.*`
  except the default `GITHUB_TOKEN` for tflint plugin download, has no Azure login step, and passes; `tests.yml` is
  unchanged and `pytest -q` runs no infra checks

## S5 (covers R2, R10, R12, R13, R15, R16, N1, N5, N6, N7, N8) [M]: No secrets committed; config and guide complete
- GIVEN `infra/` (`.tf` files, `terraform.tfvars.example`, `backend.hcl.example`, `README.md`), the root
  `README.md` and `.gitignore`
- WHEN reviewed at the gate
- THEN example files contain placeholders only and no key; `.gitignore` excludes state, `backend.hcl` and
  `*.tfvars`; the variables are exactly those in R10 with the two validations; images are pinned (no `latest`); the
  registry admin user is off; temporal ingress is internal; temporal-ui is external only with the allow-list; the
  worker, temporal and temporal-ui apps are fixed at 1 replica; the guide has every R13 section (including "Production gaps") and stays short; the
  root README keeps its target-architecture table unchanged, adds the described-vs-deployed table and links the guide

## S6 (covers N4) [M]: Application code untouched
- GIVEN the change diff against `main`
- WHEN reviewed
- THEN nothing under `ticketflow/` or `tests/` changed, nor `Dockerfile`, `docker-compose.yml`, `pyproject.toml`,
  `.env.example`

## S7 (covers R3-R9, R11, N5) [D]: Post-deploy smoke (manual, optional)
- GIVEN a deploy in fake mode following `infra/README.md`, with the deployer's IP in `temporal_ui_allowed_cidrs`
- WHEN `POST <api_url>/tickets` sends example ticket T-1 and `GET <api_url>/tickets/T-1` is polled
- THEN T-1 ends with a `reply` action; the workflow shows in `temporal_ui_url` from the allowed IP; the UI URL is
  refused from another IP; the temporal app has no public FQDN; logs with `ticket_id` appear in Log Analytics
