# Architecture — Autorater

## High-level component & data flow

```mermaid
flowchart LR
    subgraph ingestion["Ingestion stack (separate repo)"]
        LAKE[(S3 data lake\ntenants/&lt;id&gt;/...)]
    end

    subgraph aws["AWS (Terraform: iac/)"]
        EB[EventBridge\nrate schedule] -->|RunTask| MINER
        subgraph ecs["ECS Fargate cluster"]
            MINER[miner task\nPython, async]
            ALERTING[alerting service\nGo webhook x2]
        end
        DDB[(DynamoDB\nminer-state:\ncursor + lease)]
        RESULTS[(S3 results bucket\nresults/dt=YYYY-MM-DD/)]
        GLUE[Glue table\npartition projection]
        ATHENA[Athena workgroup\n+ named queries]
        CW[CloudWatch\nmetric filters + dashboard]
        SSM[/SSM parameters\n/projects/autorater/*/]
    end

    LAKE -->|ListObjectsV2\nStartAfter=cursor| MINER
    MINER <-->|cursor / lease\nconditional writes| DDB
    MINER -->|judged cases JSONL| RESULTS
    RESULTS --> GLUE --> ATHENA
    MINER -->|JSON stats line| CW
    MINER -->|severe/critical alerts| ALERTING
    ALERTING -->|high| SLACK[Slack webhook]
    ALERTING -->|critical| PD[PagerDuty Events v2]
    OR[OpenRouter API\nJUDGE_MODEL=google/gemini-2.5-flash]
    MINER <-->|LLM-as-Judge| OR
    SSM -.->|OPENROUTER_API_KEY| MINER
    SSM -.->|SLACK_WEBHOOK_URL\nPAGERDUTY_ROUTING_KEY| ALERTING
```

## Miner pipeline (per record)

```mermaid
flowchart TD
    POLL[poll record from source] --> CF[classify_failure\nretrieval / loop / truncation]
    POLL --> SC[SafetyClassifier\ninjection / self-harm / abuse / PII leak]
    CF --> WIN[sliding-window\nanomaly detector]
    SC --> WIN
    CF -->|failure case| GATE
    SC -->|safety:&lt;category&gt; case| GATE
    GATE{semantic dedup gate\nJaccard ≥ 0.8?}
    GATE -->|duplicate\nsuppressed++| DROP[drop — no API spend]
    GATE -->|novel| JUDGE[LLM-as-Judge\nOpenRouter/Gemini or mock]
    JUDGE --> SINK[(results sink\nJSONL)]
    JUDGE --> SEV{severity?}
    SEV -->|critical: injection, self-harm,\nwindow anomaly| ALERT[POST /v1/alerts]
    SEV -->|high: score ≥ 70| ALERT
    SEV -->|none| DONE[recorded only]
```

## Durable cursor & single-runner lease

```mermaid
sequenceDiagram
    participant EB as EventBridge
    participant T1 as miner task A
    participant T2 as miner task B (overlap)
    participant DDB as DynamoDB miner-state
    participant S3 as S3 data lake

    EB->>T1: RunTask (scheduled sweep)
    T1->>DDB: PutItem lease#miner (conditional)
    DDB-->>T1: acquired
    EB->>T2: RunTask (next tick, sweep A still running)
    T2->>DDB: PutItem lease#miner (conditional)
    DDB-->>T2: ConditionalCheckFailed
    T2-->>T2: exit 0 (no double-processing)
    T1->>DDB: GetItem cursor#s3://lake/tenants/
    T1->>S3: ListObjectsV2 StartAfter=cursor
    loop each new object
        T1->>S3: GetObject
        T1->>T1: mine / judge / alert
        T1->>DDB: PutItem cursor = key
    end
    T1->>DDB: DeleteItem lease (owner match)
```

A crash between processing and the cursor write re-delivers exactly the
in-flight record on the next sweep (at-least-once). Keys are date-ordered, so
`StartAfter` resume is chronological; lexically-earlier backfills are skipped
by design.

## Analytics path

```mermaid
flowchart LR
    MINER[miner sweep] -->|"results/dt=YYYY-MM-DD/&lt;sweep&gt;.jsonl"| S3[(results bucket)]
    S3 --> GLUE["Glue table judged_cases\n(partition projection on dt —\nno crawler, no MSCK)"]
    GLUE --> NQ["Athena named queries:\n• regression rate by day/tenant\n• top failure types\n• safety category volumes\n• judge usage by model"]
    MINER -->|"{'metric':'miner_stats',...}"| LOGS[CloudWatch Logs]
    LOGS --> MF[metric filters:\nJudgeCalls, JudgeFailures,\nSuppressedByDedup, SafetyFlags]
    MF --> DASH[CloudWatch dashboard\nautorater-&lt;env&gt;]
```

## Secrets flow

```mermaid
flowchart LR
    TF[Terraform iac/aws/secrets.tf\ndeclares parameters,\nignore_changes = value] -->|creates| SSM[/SSM SecureString\n/projects/autorater/*/]
    OP[operator\naws ssm put-parameter --overwrite] -->|sets real values| SSM
    SSM -->|ECS secrets valueFrom| TASKS[miner + alerting tasks\nenv at container start]
```
