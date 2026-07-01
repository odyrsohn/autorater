env                    = "dev"
vpc_subnet_ids         = ["subnet-0dev0000000000001", "subnet-0dev0000000000002"]
vpc_security_group_ids = ["sg-0dev0000000000001"]
data_lake_bucket       = "llm-ingestion-dev-data-lake-000000000000"
miner_schedule         = "rate(1 hour)"
