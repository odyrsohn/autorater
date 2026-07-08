# IaC — autorater (multi-cloud)

Two sibling Terraform roots — **both are valid deployment targets**; pick
one per environment. Full service mapping: [docs/cloud-portability.md](../docs/cloud-portability.md).

| Root | Backend | Region | Query engine | Notes |
|---|---|---|---|---|
| `aws/` | S3 (+ DynamoDB lock) | us-east-1 | Athena + Glue | provider `default_tags` |
| `azure/` | azurerm (blob-lease locking) | eastus | Synapse serverless | `local.common_tags` per resource |

```bash
cd iac/aws   && terraform init && terraform plan  -var-file=envs/dev.tfvars && terraform apply -var-file=envs/dev.tfvars
cd iac/azure && terraform init && terraform plan  -var-file=envs/dev.tfvars && terraform apply -var-file=envs/dev.tfvars
```

CI validates **both** roots on every PR; plan/apply/deploy run against the
provider selected by the repo variable `CLOUD_PROVIDER` (default `aws`).
Convention: a resource added to one root is added to the other in the same
change, or the gap is recorded in `docs/cloud-portability.md`.

Azure's Athena-named-query translations (there is no azurerm resource for
them) live in `iac/azure/synapse-queries.sql` — apply once against the
workspace's serverless SQL endpoint after `terraform apply`.

## Secrets

Terraform declares the parameters below with placeholder values and
**ignores value changes** — set the real values manually once (and again on
rotation).

**AWS** — SSM SecureString at `/projects/autorater/<NAME>`:

```bash
aws ssm put-parameter --name /projects/autorater/OPENROUTER_API_KEY \
  --type SecureString --value '<openrouter api key>' --overwrite
aws ssm put-parameter --name /projects/autorater/SLACK_WEBHOOK_URL \
  --type SecureString --value 'https://hooks.slack.com/services/…' --overwrite
aws ssm put-parameter --name /projects/autorater/PAGERDUTY_ROUTING_KEY \
  --type SecureString --value '<events v2 routing key>' --overwrite
```

**Azure** — Key Vault secret (names disallow underscores; container env
var names are unchanged):

```bash
az keyvault secret set --vault-name autoraterdevkv \
  --name OPENROUTER-API-KEY --value '<openrouter api key>'
az keyvault secret set --vault-name autoraterdevkv \
  --name SLACK-WEBHOOK-URL --value 'https://hooks.slack.com/services/…'
az keyvault secret set --vault-name autoraterdevkv \
  --name PAGERDUTY-ROUTING-KEY --value '<events v2 routing key>'
```

| Parameter | Consumed by | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | miner | LLM-as-Judge calls via OpenRouter |
| `SLACK_WEBHOOK_URL` | alerting | Slack alert channel |
| `PAGERDUTY_ROUTING_KEY` | alerting | PagerDuty Events v2 |

Non-secret env vars (`JUDGE_MODEL`, `CURSOR_TABLE`/`CURSOR_TABLE_NAME`,
`RESULTS_BUCKET`/`RESULTS_ACCOUNT_URL`, …) are declared directly in the IaC.
