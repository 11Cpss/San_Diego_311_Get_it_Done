"""Task 5 of sd311_daily: score today's new requests with the champion model.

Reads:   ml.features (today's snapshot_date)
Model:   models:/<catalog>.ml.closure_bucket@champion  (Unity Catalog registry via MLflow)
Writes:  ml.predictions  - service_request_id, snapshot_date, model_version, pred_bucket,
                           p_0_2, p_3_7, p_8_30, p_31_plus.
         Today's snapshot_date is replaced (safe re-runs); older days are kept,
         because evaluate_and_summarize compares them with labels weeks later.

All logic is in sd311_core.serving.score_frame (unit-tested). This file is glue.
If there is no champion yet (first deploy, before sd311_train ran), the task
prints a message and exits successfully, so the summary can still be written.
"""
from __future__ import annotations

import argparse


def model_name(catalog: str) -> str:
    return f"{catalog}.ml.closure_bucket"


def main(argv=None):  # pragma: no cover - needs Databricks
    import mlflow
    from mlflow import MlflowClient
    from pyspark.sql import SparkSession
    from sd311_core.registry import get_alias_version
    from sd311_core.serving import score_frame

    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="sd311")
    p.add_argument("--snapshot-date", default=None)
    p.add_argument("--model-alias", default="champion")
    args = p.parse_args(argv)
    c = args.catalog
    spark = SparkSession.builder.getOrCreate()
    mlflow.set_registry_uri("databricks-uc")
    client = MlflowClient()

    name = model_name(c)
    mv = get_alias_version(client, name, args.model_alias)
    if mv is None:
        print(f"no {name}@{args.model_alias} yet - run the sd311_train job first. "
              "Skipping scoring.")
        return
    model = mlflow.catboost.load_model(f"models:/{name}@{args.model_alias}")

    snap = args.snapshot_date or str(
        spark.sql(f"SELECT MAX(snapshot_date) FROM {c}.ml.features").first()[0])
    feats = spark.sql(
        f"SELECT * FROM {c}.ml.features WHERE snapshot_date = DATE '{snap}'").toPandas()
    preds = score_frame(model, feats, snap, model_version=mv.version)
    print(f"scored {len(preds):,} requests for {snap} with version {mv.version}")
    if preds.empty:
        return
    (spark.createDataFrame(preds).write.format("delta").mode("overwrite")
     .option("replaceWhere", f"snapshot_date = DATE '{snap}'")
     .saveAsTable(f"{c}.ml.predictions"))


if __name__ == "__main__":
    main()
