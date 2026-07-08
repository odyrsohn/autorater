env                    = "dev"
vpc_id                 = "vpc-03c2dd96406781fe5"
vpc_subnet_ids         = ["subnet-0278badd8a50951ff", "subnet-0aec7e77cef1b0a43"]
vpc_security_group_ids = ["sg-003f9722b6a24025c"]
data_lake_bucket       = "llm-ingestion-dev-data-lake-423751351671"
miner_schedule         = "rate(1 hour)"
miner_schedule_enabled = false
