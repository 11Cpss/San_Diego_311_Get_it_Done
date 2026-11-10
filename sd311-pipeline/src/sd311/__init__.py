"""sd311: AWS half of the San Diego Get It Done (311) portfolio pipeline.

Modules
-------
config         -- settings read from environment variables
logging_utils  -- one-JSON-line-per-step structured logging
schemas        -- pandera schema for the city CSV files
ingest         -- Lambda: download -> validate -> S3 -> Parquet -> Databricks
monitor        -- Lambda: read Databricks run_summary.json -> CloudWatch metrics
"""

__version__ = "0.1.0"
