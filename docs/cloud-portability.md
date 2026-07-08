# Cloud Portability — AWS ↔ Azure

Both providers are first-class deployment targets. Terraform lives in two
sibling root modules with identical variable/output conventions:

```
iac/aws/     S3 backend        us-east-1        provider default_tags   Athena/Glue
iac/azure/   azurerm backend   eastus           local.common_tags       Synapse serverless
```

The miner never references a provider directly — its state/source/results
seams are chosen by **one env var, `CLOUD_PROVIDER=aws|azure`** (unset =
local/dev fallbacks; unknown = fail fast).

## Service translation table

| Concept | AWS | Azure | Notes / gotchas |
|---|---|---|---|
| Compute schedule (miner) | **EventBridge** rule → ECS `RunTask` | **Container Apps Job** with `schedule_trigger_config` cron | Azure collapses the rule+target+pass-role chain into one resource — a cleaner fit, not just a translation. |
| Compute (alerting) | **ECS Fargate** service, scale-to-zero (`desired_count=0` baseline, orchestrated to 1 for a sweep — `step_functions.tf`) | **Container App** (2 replicas, always-on) | Not yet symmetric — see gap #6 below. |
| Registry | **ECR** | **ACR** | |
| Cursor + lease | **DynamoDB** table, conditional `PutItem` | **Table Storage**, conditional insert + ETag-guarded update | Same semantics: create-if-absent for a fresh lease, expiry check + guarded takeover otherwise. Table RowKeys forbid `/`, so URL-shaped cursor keys are hashed (original kept as a field). |
| Results storage | **S3** bucket | **ADLS Gen2** (hierarchical-namespace storage account) | HNS is what makes Synapse serverless SQL work directly over the filesystem. |
| Query engine | **Athena** + **Glue** (partition projection) | **Synapse serverless SQL** (built-in, no separate resource) | Both are pay-per-query, no cluster. Glue's partition projection has no Synapse equivalent — queries use `filepath()` over the `dt=*/` layout instead (`iac/azure/synapse-queries.sql`). Athena named queries have no first-class azurerm resource; the seven translations ship as a `.sql` file, applied manually or via a SQL-script CD step. |
| Secrets | **SSM Parameter Store** SecureString | **Key Vault** secret | Same convention: declared with `ignore_changes = [value]`, set manually. KV secret names disallow underscores (`OPENROUTER-API-KEY` vs `OPENROUTER_API_KEY`) — container env var names are unchanged. |
| Logs + ad-hoc queries | **CloudWatch Logs** + Logs Insights | **Log Analytics** + KQL | Same five saved on-call slices (`by-tenant`, `by-failure-mode`, `by-language`, `by-client`, `by-model`/`by-serving-model`). |
| Metric-driven alert (alert-storm) | CloudWatch **metric filter** → **metric alarm** (two resources) | **Scheduled query rule** (one resource, KQL-driven) | Azure's log-based alert collapses the filter+alarm pair. |
| Tracing | **X-Ray** (10% sampling rule) | **Application Insights** (`sampling_percentage = 10`) | |
| Workload identity | ECS task role | User-assigned **managed identity** | SDK auth: AWS default chain ≙ `DefaultAzureCredential`. |
| Cost tags | provider `default_tags` | `local.common_tags` per resource | Same `app:*` keys. |
| Terraform state | S3 backend + DynamoDB lock | `azurerm` backend (blob-lease locking) | |
| CI auth | GitHub OIDC → IAM role | GitHub OIDC → federated credential (`azure/login`) | Both keyless. |

## App-layer provider matrix (this repo)

| Seam | AWS impl | Azure impl | Selecting env vars |
|---|---|---|---|
| `CursorStore` (`miner/miner/sources.py`) | `DynamoCursorStore` | `TableCursorStore` (`azure_sources.py`) | `CURSOR_TABLE` \| `CURSOR_TABLE_ENDPOINT`+`CURSOR_TABLE_NAME` |
| `Source` (poll ingested traffic) | `S3Source` | `BlobSource` | `DATA_LAKE_BUCKET` \| `DATA_LAKE_ACCOUNT_URL`+`DATA_LAKE_CONTAINER` |
| `ResultsSink` | `S3ResultsSink` | `BlobResultsSink` | `RESULTS_BUCKET` \| `RESULTS_ACCOUNT_URL`+`RESULTS_CONTAINER` |

Selection lives in `cursor_store_from_env`, `source_from_env`,
`results_sink_from_env` (`sources.py`/`results.py`). Azure SDKs
(`azure-identity`, `azure-storage-blob`, `azure-data-tables`) are lazily
imported exactly like boto3 — local dev and unit tests need neither
cloud's SDK; adapters are unit-tested against fakes implementing the small
`_AzureTableAdapter`/`_AzureContainerAdapter` seams
(`tests/test_azure_adapters.py`).

Behavioral difference worth knowing: `BlobSource` has no `StartAfter`
equivalent for listing, so resume filters the (lexicographically ordered)
listing client-side — same correctness and chronological-resume guarantee
as `S3Source`, but O(prefix) listing cost instead of O(unprocessed).

## First deployment on Azure

```bash
cd iac/azure
terraform init && terraform apply -var-file=envs/dev.tfvars
az keyvault secret set --vault-name $(terraform output -raw key_vault_name) \
  --name OPENROUTER-API-KEY --value 'sk-or-...'
# apply the Synapse query translations once, via the workspace's serverless
# SQL endpoint (terraform output synapse_serverless_endpoint):
#   sqlcmd -S <endpoint> -d master -i synapse-queries.sql -G
```

CI: set the repo variable `CLOUD_PROVIDER=azure` + `AZURE_CLIENT_ID`/
`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` secrets. Both roots always
validate in CI regardless of the active provider.

## Known translation gaps (this repo)

1. **Athena named queries have no azurerm resource** — Synapse serverless
   query translations ship as a plain `.sql` file
   (`iac/azure/synapse-queries.sql`), not Terraform-managed views; apply
   manually or add a CD step.
2. **No Glue partition projection equivalent** — Synapse queries always
   `filepath()`-filter on `dt` explicitly; there is no catalog-level
   partition awareness to enforce this the way Glue's projection does.
3. **Blob listing has no `StartAfter`** — `BlobSource` resumes via
   client-side filtering (documented above); fine at this pipeline's scale,
   worth revisiting for very large data lakes.
4. **Container Apps Job cron vs EventBridge rate**: `iac/aws` uses
   `rate(15 minutes)`; the Azure cron (`*/15 * * * *`) is the direct
   translation, but Azure Container Apps Jobs have their own
   timezone/precision semantics — verify against the SLA you actually need.
5. Trivy's config scanner runs on both roots in CI, but its azurerm
   ruleset has less first-party coverage than its AWS ruleset — expect a
   different noise profile.
6. **Alerting scale-to-zero has no Azure twin yet.** `iac/aws` added a
   Step Functions state machine (`step_functions.tf`) that scales the
   alerting ECS service from 0 to 1 for the duration of a miner sweep and
   back to 0 afterward, plus a Cloud Map private DNS namespace
   (`service_discovery.tf`) so the miner can still resolve it at a stable
   name. `iac/azure` still runs alerting as an always-on 2-replica
   Container App. Azure Container Apps have native KEDA-based scale-to-zero
   (`min_replicas = 0` + a scale rule) — likely a *simpler* fix on that side
   than the AWS orchestration, not just a translation. Also added on the
   AWS side: `miner_schedule_enabled` (bool) lets an environment disable
   the EventBridge cron and trigger sweeps manually via
   `aws stepfunctions start-execution`; Azure's Container Apps Job has no
   equivalent toggle yet.
