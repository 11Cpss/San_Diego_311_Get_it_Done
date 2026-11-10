# Earlier analysis notebooks

I started this project with notebooks about service delays, district patterns, and CatBoost regression. I kept them here to show how the project developed. The current AWS ingestion pipeline and closure-time model are in [`../../sd311-pipeline`](../../sd311-pipeline/README.md).

The numbered notebooks, `finding.ipynb`, `early_eda/`, and `models/` are historical experiments. The original notebooks expect annual city CSVs under `csv_files/`; those large raw files are not stored in Git. `requirements.txt` is the environment used by this earlier work.

These notebooks are separate from the current daily pipeline, which uses a Pandera data contract and a time-aware CatBoost classification backtest.
