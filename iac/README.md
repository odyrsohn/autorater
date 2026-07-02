# IaC — autorater

Terraform stack (S3 backend, `us-east-1`). Per-env values: `envs/dev.tfvars`.

```bash
terraform init
terraform plan  -var-file=envs/dev.tfvars
terraform apply -var-file=envs/dev.tfvars
```

## Secrets (SSM Parameter Store)

Terraform creates the parameters below under `/projects/autorater/` as
SecureStrings with placeholder values and **ignores value changes** — set the
real values manually once (and again whenever they rotate):

```bash
aws ssm put-parameter --name /projects/autorater/OPENROUTER_API_KEY \
  --type SecureString --value '<openrouter api key>' --overwrite

aws ssm put-parameter --name /projects/autorater/SLACK_WEBHOOK_URL \
  --type SecureString --value 'https://hooks.slack.com/services/…' --overwrite

aws ssm put-parameter --name /projects/autorater/PAGERDUTY_ROUTING_KEY \
  --type SecureString --value '<events v2 routing key>' --overwrite
```

| Parameter | Consumed by | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | miner task | LLM-as-Judge calls via OpenRouter |
| `SLACK_WEBHOOK_URL` | alerting task | Slack alert channel |
| `PAGERDUTY_ROUTING_KEY` | alerting task | PagerDuty Events v2 |

ECS task definitions consume them via the `secrets` block; non-secret env
vars (e.g. `JUDGE_MODEL`, `CURSOR_TABLE`, `RESULTS_BUCKET`) are declared
directly in the IaC (`ecs.tf`).
