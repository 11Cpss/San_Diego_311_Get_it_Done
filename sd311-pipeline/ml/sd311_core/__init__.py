"""sd311_core: small, readable building blocks for the San Diego 311
"Get It Done" closure-time model (v2).

Modules
-------
io        load raw files, infer the open-file extract date
cleaning  the cleaning rules, written once
labels    4-bucket closure-time labels with an explicit "as-of" date
features  submission-time (leakage-safe) features
baseline  per-service trailing bucket-mix baseline
train     CatBoost MultiClass training helpers
backtest  rolling-origin backtest
metrics   shared evaluation metrics
"""
