-- Synapse serverless SQL translations of the seven Athena named queries
-- (iac/aws/analytics.tf locals.named_queries). Run in the workspace's
-- built-in serverless pool. Partition projection has no Glue equivalent
-- here: filepath(1) extracts the dt=YYYY-MM-DD segment from the path —
-- ALWAYS filter on it, exactly like filtering on Athena's dt partition key.
--
-- Base pattern (CETAS-free, straight over the JSONL):
--   FROM OPENROWSET(
--     BULK 'https://<results-account>.dfs.core.windows.net/results/results/dt=*/*.jsonl',
--     FORMAT = 'CSV', FIELDTERMINATOR = '0x0b', FIELDQUOTE = '0x0b'
--   ) WITH (line NVARCHAR(MAX)) AS raw
-- then JSON_VALUE(line, '$.field').  A reusable view keeps that noise out
-- of every query:

CREATE OR ALTER VIEW dbo.judged_cases AS
SELECT
    raw.filepath(1)                                        AS dt,
    JSON_VALUE(line, '$.case_id')                          AS case_id,
    JSON_VALUE(line, '$.tenant_id')                        AS tenant_id,
    JSON_VALUE(line, '$.failure_mode')                     AS failure_mode,
    JSON_VALUE(line, '$.judge_category')                   AS judge_category,
    JSON_VALUE(line, '$.lang')                             AS lang,
    JSON_VALUE(line, '$.client_platform')                  AS client_platform,
    JSON_VALUE(line, '$.client_os_version')                AS client_os_version,
    JSON_VALUE(line, '$.serving_model')                    AS serving_model,
    CAST(JSON_VALUE(line, '$.score') AS INT)               AS score,
    JSON_VALUE(line, '$.verdict')                          AS verdict,
    JSON_VALUE(line, '$.rationale')                        AS rationale,
    JSON_VALUE(line, '$.model')                            AS model,
    CAST(JSON_VALUE(line, '$.window_failure_rate') AS FLOAT) AS window_failure_rate,
    CAST(JSON_VALUE(line, '$.alerted') AS BIT)             AS alerted,
    JSON_VALUE(line, '$.sweep_id')                         AS sweep_id,
    JSON_VALUE(line, '$.ts')                               AS ts,
    line                                                   AS _raw -- safety_categories array via OPENJSON when needed
FROM OPENROWSET(
    BULK 'https://RESULTS_ACCOUNT.dfs.core.windows.net/results/results/dt=*/*.jsonl',
    FORMAT = 'CSV', FIELDTERMINATOR = '0x0b', FIELDQUOTE = '0x0b'
) WITH (line NVARCHAR(MAX)) AS raw;
GO

-- 1. regression-rate-by-day-tenant
SELECT dt, tenant_id,
       COUNT(*)                                            AS judged_cases,
       AVG(CAST(score AS FLOAT))                           AS avg_score,
       SUM(CASE WHEN verdict = 'regression' THEN 1 ELSE 0 END) AS regressions,
       SUM(CASE WHEN alerted = 1 THEN 1 ELSE 0 END)        AS alerts
FROM dbo.judged_cases
WHERE dt >= FORMAT(DATEADD(day, -30, GETUTCDATE()), 'yyyy-MM-dd')
GROUP BY dt, tenant_id
ORDER BY dt DESC, regressions DESC;

-- 2. top-failure-types
SELECT failure_mode, judge_category, COUNT(*) AS cases, AVG(CAST(score AS FLOAT)) AS avg_score
FROM dbo.judged_cases
WHERE dt >= FORMAT(DATEADD(day, -7, GETUTCDATE()), 'yyyy-MM-dd')
GROUP BY failure_mode, judge_category
ORDER BY cases DESC;

-- 3. safety-category-volumes (UNNEST ≙ OPENJSON over the raw line)
SELECT jc.dt, cat.[value] AS safety_category, COUNT(*) AS findings
FROM dbo.judged_cases jc
CROSS APPLY OPENJSON(jc._raw, '$.safety_categories') AS cat
WHERE jc.dt >= FORMAT(DATEADD(day, -30, GETUTCDATE()), 'yyyy-MM-dd')
GROUP BY jc.dt, cat.[value]
ORDER BY jc.dt DESC, findings DESC;

-- 4. judge-usage-by-model (the JUDGE's model)
SELECT dt, model, COUNT(*) AS judged_cases, AVG(CAST(score AS FLOAT)) AS avg_score
FROM dbo.judged_cases
WHERE dt >= FORMAT(DATEADD(day, -30, GETUTCDATE()), 'yyyy-MM-dd')
GROUP BY dt, model
ORDER BY dt DESC;

-- 5. failure-rate-by-serving-model ("Claude slice vs OSS-fallback slice")
SELECT dt, serving_model,
       COUNT(*)                                                AS judged_cases,
       SUM(CASE WHEN verdict = 'regression' THEN 1 ELSE 0 END) AS regressions,
       ROUND(1.0 * SUM(CASE WHEN verdict = 'regression' THEN 1 ELSE 0 END) / COUNT(*), 4) AS regression_rate
FROM dbo.judged_cases
WHERE dt >= FORMAT(DATEADD(day, -30, GETUTCDATE()), 'yyyy-MM-dd')
  AND serving_model IS NOT NULL
GROUP BY dt, serving_model
ORDER BY dt DESC, regression_rate DESC;

-- 6. regressions-by-language ("Spanish-only prompt regressions")
SELECT dt, lang, failure_mode, COUNT(*) AS regressions
FROM dbo.judged_cases
WHERE verdict = 'regression'
  AND lang LIKE 'es%'
  AND dt >= FORMAT(DATEADD(day, -30, GETUTCDATE()), 'yyyy-MM-dd')
GROUP BY dt, lang, failure_mode
ORDER BY dt DESC, regressions DESC;

-- 7. failures-by-client ("AAOS 12 / ChromeOS slice")
SELECT dt, client_platform, client_os_version, failure_mode, COUNT(*) AS cases
FROM dbo.judged_cases
WHERE client_platform IS NOT NULL
  AND dt >= FORMAT(DATEADD(day, -30, GETUTCDATE()), 'yyyy-MM-dd')
GROUP BY dt, client_platform, client_os_version, failure_mode
ORDER BY dt DESC, cases DESC;
