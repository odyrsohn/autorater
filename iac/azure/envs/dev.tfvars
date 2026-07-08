env                   = "dev"
data_lake_account_url = "https://llmingestiondevlake.blob.core.windows.net/"
data_lake_container   = "data-lake"
miner_cron            = "0 * * * *" # hourly in dev (aws dev uses rate(1 hour))
