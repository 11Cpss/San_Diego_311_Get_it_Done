"""Register the local bronze Parquet files as Spark tables bronze.open_snapshots /
bronze.closed_requests, so `dbt build --target spark_local` can read them.

Run from the directory where dbt will run (Spark keeps its metastore_db there).
A sample keeps it quick on a laptop: --frac 0.05 by default.
"""
import argparse
from pathlib import Path

import pandas as pd
from pyspark.sql import SparkSession

p = argparse.ArgumentParser()
p.add_argument("--bronze", required=True, help="folder with open_snapshots.parquet / closed_requests.parquet")
p.add_argument("--frac", type=float, default=0.05)
args = p.parse_args()

work = Path.cwd() / "spark_bronze"
spark = SparkSession.builder.enableHiveSupport().config("spark.ui.enabled", "false").getOrCreate()
spark.sql("CREATE DATABASE IF NOT EXISTS bronze")
for t in ("open_snapshots", "closed_requests"):
    df = pd.read_parquet(Path(args.bronze) / f"{t}.parquet").sample(frac=args.frac, random_state=1)
    df["_ingested_at"] = df["_ingested_at"].astype("datetime64[us]")  # Spark-friendly precision
    (work / t).mkdir(parents=True, exist_ok=True)
    df.to_parquet(work / t / "part0.parquet", index=False)
    spark.sql(f"DROP TABLE IF EXISTS bronze.{t}")
    spark.sql(f"CREATE TABLE bronze.{t} USING parquet LOCATION '{work / t}'")
    print(f"bronze.{t}: {spark.table(f'bronze.{t}').count():,} rows")
