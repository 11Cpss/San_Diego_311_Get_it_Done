"""Only task of the manual sd311_train job: backtest, train, register, set alias.

Steps
  1. Read silver.requests_clean (dbt, all history) with Spark -> pandas.
  2. Same pipeline as ml/scripts: clean_requests -> make_labels(as_of) ->
     build_features. as_of = today's Pacific date.
  3. Rolling-origin backtest (sd311_core.backtest.run_fold) on the last N
     quarters whose labels are final; log per-fold and mean metrics and a
     figure to MLflow.
  4. Fit the final model on the newest window (same make_fold boundaries as
     ml/scripts/fit_final.py), log it with a signature (Unity Catalog requires one),
     register it as <catalog>.ml.closure_bucket.
  5. Champion/challenger: sd311_core.registry.assign_alias, which applies
     sd311_core.monitoring.promotion_decision:
        champion   if mean backtest log-loss < current champion's AND
                   macro-F1 > baseline in every fold
        challenger otherwise
  6. Log the training reference distributions (service_name, case_origin shares)
     as reference_distributions.json - evaluate_and_summarize reads it for PSI.

Memory note: this is the one task that pulls the full history into pandas
(~2.5M rows x ~30 columns, a few GB). Training samples 300k rows per window,
like the local backtest. If serverless memory is too small, pass --since to
limit history (e.g. --since 2023-01-01).
"""
from __future__ import annotations

import argparse
import datetime as dt
from zoneinfo import ZoneInfo


def main(argv=None):  # pragma: no cover - needs Databricks
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import mlflow
    import numpy as np
    import pandas as pd
    from mlflow import MlflowClient
    from mlflow.models import infer_signature
    from pyspark.sql import SparkSession
    from sd311_core.backtest import make_fold, quarterly_origins, run_fold
    from sd311_core.cleaning import clean_requests
    from sd311_core.features import CATEGORICAL_FEATURES, FEATURES, build_features
    from sd311_core.labels import make_labels
    from sd311_core.monitoring import reference_distributions
    from sd311_core.registry import MEAN_LOG_LOSS_METRIC, assign_alias
    from sd311_core.serving import prepare_requests
    from sd311_core.train import DEFAULT_PARAMS, fit_model

    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="sd311")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today, Pacific)")
    p.add_argument("--n-folds", type=int, default=4)
    p.add_argument("--max-train-rows", type=int, default=300_000)
    p.add_argument("--since", default=None, help="only read requests submitted on/after this date")
    p.add_argument("--experiment", default="/Shared/sd311_closure_bucket")
    args = p.parse_args(argv)
    c = args.catalog
    as_of = pd.Timestamp(args.as_of or dt.datetime.now(ZoneInfo("America/Los_Angeles")).date())

    spark = SparkSession.builder.getOrCreate()
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(args.experiment)
    client = MlflowClient()
    name = f"{c}.ml.closure_bucket"

    where = f"WHERE CAST(date_requested AS DATE) >= DATE '{args.since}'" if args.since else ""
    raw = spark.sql(f"SELECT * FROM {c}.silver.requests_clean {where}").toPandas()
    df = clean_requests(prepare_requests(raw, "closed"))
    del raw
    labels = make_labels(df, as_of)
    data = df[["service_request_id", "date_requested"]].join(labels).join(
        build_features(df, as_of=as_of, labels=labels))
    print(f"rows {len(data):,}, labeled {data['bucket'].notna().sum():,}, as_of {as_of.date()}")

    with mlflow.start_run(run_name=f"train_{as_of.date()}") as run:
        mlflow.log_params({"as_of": str(as_of.date()), "n_folds": args.n_folds,
                           "max_train_rows": args.max_train_rows,
                           **{k: v for k, v in DEFAULT_PARAMS.items() if k != "class_names"}})
        folds = []
        for i, origin in enumerate(quarterly_origins(as_of, args.n_folds)):
            res, _, _ = run_fold(data, make_fold(origin), max_train_rows=args.max_train_rows)
            folds.append(res)
            for who in ("model", "baseline"):
                for m in ("macro_f1", "log_loss", "accuracy"):
                    mlflow.log_metric(f"fold{i}_{who}_{m}", res[who][m])
            print(f"fold {origin.date()}: model F1 {res['model']['macro_f1']:.3f} "
                  f"baseline {res['baseline']['macro_f1']:.3f}")
        mean_ll = float(np.mean([f["model"]["log_loss"] for f in folds]))
        mlflow.log_metric(MEAN_LOG_LOSS_METRIC, mean_ll)
        mlflow.log_metric("backtest_mean_macro_f1",
                          float(np.mean([f["model"]["macro_f1"] for f in folds])))
        mlflow.log_dict({"folds": folds}, "backtest.json")

        fig, ax = plt.subplots(figsize=(6, 3))
        x = np.arange(len(folds))
        ax.bar(x - 0.2, [f["model"]["macro_f1"] for f in folds], 0.4, label="model")
        ax.bar(x + 0.2, [f["baseline"]["macro_f1"] for f in folds], 0.4, label="baseline")
        ax.set_xticks(x, [f["fold"]["test_start"] for f in folds])
        ax.set_ylabel("macro-F1")
        ax.legend()
        fig.tight_layout()
        mlflow.log_figure(fig, "fold_macro_f1.png")

        # Final model: newest window, same boundaries as ml/scripts/fit_final.py.
        fold = make_fold(as_of + pd.Timedelta(days=1))
        t = data["date_requested"]
        lab = data["bucket"].notna()
        train = data[(t >= fold.train_start) & (t < fold.valid_start) & lab]
        valid = data[(t >= fold.valid_start) & (t < fold.valid_end) & lab]
        if len(train) > args.max_train_rows:
            train = train.sample(args.max_train_rows, random_state=0)
        model = fit_model(train, valid)
        mlflow.log_params({"final_train_start": str(fold.train_start.date()),
                           "final_valid_end": str(fold.valid_end.date()),
                           "final_n_train": len(train)})
        mlflow.log_dict(reference_distributions(df.loc[train.index]),
                        "reference_distributions.json")

        X = train[FEATURES].head(200).copy()
        for col in CATEGORICAL_FEATURES:
            X[col] = X[col].astype(str)
        sig = infer_signature(X, model.predict_proba(X))
        info = mlflow.catboost.log_model(model, name="model", signature=sig)
        mv = mlflow.register_model(info.model_uri, name)
        decision = assign_alias(client, name, mv.version, folds)
        mlflow.log_dict(decision, "promotion_decision.json")
        print(f"registered {name} v{mv.version} as @{decision['alias']}: {decision['reason']}")
        print(f"run {run.info.run_id}")


if __name__ == "__main__":
    main()
