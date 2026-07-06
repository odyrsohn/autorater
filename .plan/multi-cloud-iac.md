# Multi-Cloud IaC (AWS + Azure) + Provider-Agnostic App Layer

## Context

All three repos (`multi-tenant-ingestion`, `data-versioning`, `autorater`) currently deploy only to
AWS (`iac/` root, S3 backend, us-east-1). The user wants **Azure as an equally valid deployment
target**, structured per IaC best practices, with **comparison documentation** (service translation
table: S3↔Blob, SQS↔Service Bus, …) and — per the scoping answer — the **app layer decoupled from
providers** so switching clouds is configuration, not code surgery. Athena/Glue translates to
**Synapse serverless** (user-selected).

Current state I authored and know exactly: flat `iac/` per repo (AWS provider `default_tags`,
S3 backend); apps already isolate cloud calls behind interfaces (`queue.Publisher`,
`storage.Deleter`, `Committer`, `CursorStore`, `ResultsSink`, `Source`) with lazy boto3 imports and
`*_from_env()` factories — the exact seams the adapters plug into.

## Structure decision (IaC best practice)

Terraform cannot conditionally disable a provider in one root, so multi-cloud = **one root module
per provider, sharing conventions**:

```
iac/
├── aws/        # current files, git mv (S3 backend, unchanged resources)
├── azure/      # new root (azurerm backend; azurerm requires `features {}`)
│   └── envs/dev.tfvars        (where the aws root has one)
└── README.md   # provider selection, commands, secrets for BOTH providers
docs/cloud-portability.md      # the comparison/translation document
```

Shared conventions across roots: identical variable names (`app_name`, `env`, tag values…),
symmetric output names (queue endpoint, storage name, secrets prefix) so app env wiring is uniform.
Azure has **no provider-level `default_tags`** → `local.common_tags` merged into every resource +
resource-group tags; the doc explains Azure Policy tag-inheritance as the enforcement equivalent.
Azure state backend: `azurerm` (storage account bootstrapped out-of-band; blob lease = built-in
locking ↔ S3+DynamoDB lock table).

## Provider-agnostic app layer (selection convention)

One env var, `CLOUD_PROVIDER` (`aws` | `azure`; absent ⇒ local/dev fallbacks as today). The
existing `*_from_env()` factories branch on it; Azure SDKs are lazily imported exactly like boto3
(tests stay hermetic; local mode needs no cloud deps). New adapters:

| Interface (existing seam) | AWS impl (exists) | Azure impl (NEW) | Env |
|---|---|---|---|
| Go `queue.Publisher` (ingestion) | `SQSPublisher` | `ServiceBusPublisher` (`azservicebus`) | `SERVICEBUS_NAMESPACE`+`SERVICEBUS_QUEUE` |
| Py worker consume loop | `run_sqs_loop` | `run_servicebus_loop` (`azure-servicebus`) | same |
| Py `storage` committer | `S3Committer` | `BlobCommitter` (`azure-storage-blob`) | `BLOB_ACCOUNT_URL`+`BLOB_CONTAINER` |
| Go `storage.Deleter` (wipeout) | **gap — only DirDeleter exists** → add `S3Deleter` | add `BlobDeleter` (`azblob`) | `TIER_PROVIDER=dir\|s3\|azure` + per-tier bucket/container vars |
| Py miner `Source` | `S3Source` | `BlobSource` | `DATA_LAKE_*` |
| Py miner `CursorStore` | `DynamoCursorStore` | `TableCursorStore` (`azure-data-tables`; same conditional-insert lease via If-Match/ETag) | `CURSOR_TABLE_URL` |
| Py miner `ResultsSink` | `S3ResultsSink` | `BlobResultsSink` | `RESULTS_*` |
| Go alerting | no cloud SDK (webhooks + env secrets) | nothing needed — already agnostic | — |

Auth: Azure impls use `DefaultAzureCredential` (managed identity in Container Apps, `az login`
locally) — the doc maps this to the AWS default credential chain. Unit tests stub the Azure clients
mirroring the existing `FakeS3Client` pattern in `autorater/miner/tests/test_sources.py`.

## Azure resources per repo (`iac/azure/`)

Common to all: resource group, `local.common_tags`, Log Analytics workspace, ACR, Container Apps
environment, Key Vault (RBAC mode), managed identities + role assignments, `azurerm` backend block.

**multi-tenant-ingestion**
- SQS+DLQ → **Service Bus** namespace + queue (`max_delivery_count = 5`, dead-lettering on) — DLQ is
  built into the queue, not a second resource (doc point).
- S3 data lake + KMS → **Storage account + container** with blob versioning, soft delete, CMK from
  Key Vault, lifecycle policy Hot→Cool (30d)→Archive (180d), delete old versions (30d).
- Per-tenant IAM prefix policies → **ABAC role assignments** (`Storage Blob Data Contributor` with
  a blob-path-prefix condition per tenant) — the honest translation of `s3:prefix` conditions.
- SNS + queue alarms → **Action Group** (email via `ops_alert_email`) + Azure Monitor metric alerts
  on Service Bus `DeadletteredMessages` (not-empty + rising) and `ActiveMessages` backlog. Gap to
  document: Service Bus exposes no oldest-message-age metric (AWS `ApproximateAgeOfOldestMessage`
  has no direct equivalent).
- Dashboards → `azurerm_portal_dashboard` ×2 (ops + business; business = storage capacity metrics,
  Service Bus volume, `azurerm_consumption_budget_resource_group` + Cost Management doc link).
- Saved Logs Insights queries → **`azurerm_log_analytics_saved_search`** ×5 in KQL over
  `ContainerAppConsoleLogs_CL` parsing the JSON envelope (`parse_json(Log_s)`); metric filters →
  scheduled-query alerts where needed.
- Container Apps ×2 (ingestion, worker) wired with managed identity + Service Bus/Blob RBAC.

**data-versioning**
- 3 tier buckets + lifecycle → one storage account, **3 containers** (hot/cold/archive) with
  per-container-prefix lifecycle rules (tierToCool / tierToArchive / delete noncurrent versions =
  the soft-delete→permanent-wipe window), blob versioning.
- SSM `API_TOKEN` → **Key Vault secret** with `ignore_changes = [value]` (same manual-value
  convention; `az keyvault secret set` command in `iac/README.md`).
- ECS task def → **Container App** (orchestrator) with Key Vault secret reference.
- 4 saved searches (KQL translations of by-tenant / by-failure-mode / stuck-sagas / wipeout-audit).

**autorater**
- ECS + EventBridge cron → **Container App** (alerting, 2 replicas) + **Container Apps Job** with
  cron trigger (the miner — a cleaner fit than EventBridge→RunTask, worth a doc callout).
- DynamoDB cursor/lease → **Table Storage** table (conditional insert = lease).
- S3 results + Glue + Athena → **ADLS Gen2** (`is_hns_enabled`) results filesystem + **Synapse
  workspace** (built-in serverless SQL, pay-per-query like Athena). Named queries can't be
  first-class azurerm resources → ship `iac/azure/synapse-queries.sql` with OPENROWSET translations
  of all 7 canned queries (partition-projection ↔ `filepath()` functions on `dt=*/` paths).
- Key Vault secrets ×3 (OPENROUTER_API_KEY, SLACK_WEBHOOK_URL, PAGERDUTY_ROUTING_KEY).
- X-Ray → **Application Insights**; alert-storm alarm → scheduled-query alert counting
  `alert_dispatched` events; 5 saved searches (KQL slices).

## Docs — `docs/cloud-portability.md` (per repo: shared table + repo-specific section)

1. **Translation table** (the explicit ask): S3↔Blob/ADLS Gen2, SQS+DLQ↔Service Bus (+built-in
   DLQ), IAM prefix policy↔RBAC+ABAC condition, KMS CMK↔Key Vault key, SSM SecureString↔Key Vault
   secret, DynamoDB↔Table Storage, ECS Fargate↔Container Apps, EventBridge cron↔Container Apps Job,
   ECR↔ACR, CloudWatch metrics/alarms↔Azure Monitor, CW Logs+Logs Insights↔Log Analytics+KQL,
   `query_definition`↔`saved_search`, SNS↔Action Group, X-Ray↔App Insights, Glue+Athena↔Synapse
   serverless, `default_tags`↔RG tags+Policy inheritance, Cost Explorer↔Cost Management,
   S3 backend+Dynamo lock↔azurerm backend+blob lease.
2. **Similarities/differences notes**: storage-account naming constraints (3–24 lowercase, global),
   soft-delete semantics, missing message-age metric, ABAC syntax, credential chains
   (AWS default chain ↔ `DefaultAzureCredential`), tag enforcement models.
3. **App-layer provider matrix** (the table above) + `CLOUD_PROVIDER` convention and per-service
   env-var matrix for both clouds.

## CI (`.github/workflows/ci-cd.yml`, all repos)

- `terraform-checks`: matrix `provider: [aws, azure]`, working dir `iac/${{ matrix.provider }}`.
- `terraform-plan`/`apply`/deploy: working dir `iac/${{ vars.CLOUD_PROVIDER || 'aws' }}`;
  conditional auth step — `aws-actions/configure-aws-credentials` vs `azure/login` (both OIDC);
  deploy pushes to ECR/ECS vs ACR/Container Apps based on the same variable.

## Other updates

- **Specs**: infra specs per repo (`SPEC-004-pipeline-infra`, `SPEC-004-storage-tiers-infra`,
  autorater SPEC-004/005) gain REQs for dual-root layout, `CLOUD_PROVIDER` adapter selection, and
  tag-convention difference; anchors updated. `docs/architecture.md` secrets/infra diagrams get an
  Azure lane or note.
- **CLAUDE.md** each repo: dual-root commands, `CLOUD_PROVIDER` convention, "keep roots symmetric"
  rule. `add-iac-resource` skill (multi-tenant-ingestion): resources must be added to BOTH roots or
  the gap documented in cloud-portability.md.
- **Dependencies**: Go `azservicebus`/`azblob` (ingestion, orchestrator); Python pins
  `azure-storage-blob`, `azure-servicebus`, `azure-data-tables`, `azure-identity` (lazy-imported).
- Branch per repo: `feature/azure-iac` stacked on `feature/standardized-logging`; push, **no PRs**.

## Execution order (per repo, ingestion → data-versioning → autorater)

1. `git mv iac/* iac/aws/` (+ fix CI paths), add `iac/azure/` root, validate both.
2. App adapters + factory branching + unit tests (stubbed clients).
3. `docs/cloud-portability.md`, `iac/README.md` (both providers' commands/secrets), specs, CLAUDE.md.
4. Full verification, commit, push branch.

## Verification

1. `terraform fmt -check` + `init -backend=false` + `validate` on **all six roots** (azurerm
   validates offline; no Azure credentials needed).
2. Go: `go vet`, `go test -race` (new Azure deps compile; adapters unit-tested with fakes).
   Python: `unittest` + ruff (Azure adapters tested via stubbed clients, lazy imports keep local
   runs dependency-free).
3. Factory selection tests: `CLOUD_PROVIDER=azure` env picks Azure impls, unset falls back to
   local/dev exactly as today.
4. Existing local E2E smokes unchanged and re-run (they use local fallbacks — proves the
   decoupling didn't disturb the default path).
5. CI YAML: matrix renders both providers; grep-check that no job hardcodes `iac/` without a
   provider segment.
6. No live Azure apply (no credentials in this environment) — deployability is proven by validate +
   documented in the portability doc's "first deployment" section.
