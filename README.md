# San Diego Get It Done: Service Delay Analysis and Prediction

End-to-end analysis of San Diego's 311-style **Get It Done** requests, focused on:
- uncovering geographic and service-level response-time disparities,
- testing structural hypotheses with mixed-effects models, and
- predicting closure time with tree-based ML.

This repo centers on three notebooks:
- `01_citywide_data_preprocessing_eda.ipynb`
- `02_council_district_eda.ipynb`
- `03_catboost_modeling.ipynb`

A summary notebook (`finding.ipynb`) captures consolidated results and writeups.

---

## Project Scope

**Main question:** what drives slower response times (`case_age_days`) in the city request system?

We approach this from three angles:
1. **Citywide trend and feature analysis** (all years)
2. **District and neighborhood equity analysis** (with service-composition context)
3. **Predictive modeling** (time-split CatBoost + baseline comparisons)

---

## Data

Raw data files live under `csv_files/` and include annual closed-case extracts:
- `get_it_done_requests_closed_20*_datasd.csv`

Core fields used across analyses include:
- `service_request_id`
- `date_requested`, `date_closed`
- `case_age_days`
- `service_name`, `service_name_detail`
- `council_district`, `comm_plan_name`
- `case_origin`

---

## Notebook Guide

### 1) `01_citywide_data_preprocessing_eda.ipynb`
Citywide cleaning + feature engineering + high-level trends.

What it does:
- Loads annual closed-request files and combines into a master table.
- Cleans key categorical fields and invalid wait times.
- Engineers temporal/request features.
- Explores distributional behavior and correlation structure for response time.

Use this notebook for:
- first-pass data quality checks,
- system-wide trend context,
- feature intuition before modeling.

### 2) `02_council_district_eda.ipynb`
District/neighborhood disparity deep dive + mixed-effects analysis.

What it does:
- Quantifies median response differences by district and neighborhood.
- Compares Districts 5 & 6 vs other districts over time.
- Tests composition effects (infrastructure-heavy request mix).
- Runs cross-classified mixed-effects models with random effects for neighborhood and request type.
- Includes 90/95/99 sensitivity analyses.

Documented findings include:
- Districts 5 & 6 had substantially longer waits overall.
- Districts 5 & 6 had ~**2.45x** higher infrastructure-request share in one comparison slice.
- Pavement maintenance showed a large service-specific gap (documented in findings writeup).
- Mixed-effects model (2024–2025 cohort) found a **negative** association between neighborhood request share and wait time (`beta = -1.032`, `p < 0.001` in full spec documented in `finding.ipynb`).

### 3) `03_catboost_modeling.ipynb`
Predictive modeling workflow.

What it does:
- Builds modeling dataset and engineered predictors.
- Uses time-based train/validation/test splits.
- Trains `CatBoostRegressor` with categorical handling.
- Compares against a median baseline.
- Runs Optuna hyperparameter tuning.
- Experiments with stronger categorical and interaction features (e.g., cleaned service detail and service-neighborhood interactions).

Use this notebook for:
- reproducible predictive experiments,
- feature ablation/tuning loops,
- benchmark comparison and error analysis.

### 4) `finding.ipynb`
Narrative summary of key outcomes from analysis/modeling tracks.

---

## Key Findings (Current)

From the documented analyses and summary notes:
- **Geographic disparity exists** in overall response times, especially in Districts 5 & 6 compared with other districts.
- A large portion of disparity appears linked to **request composition** (higher concentration of slower infrastructure services).
- There is a notable service-specific gap in **pavement maintenance** response time for Districts 5 & 6.
- Mixed-effects modeling supports the hypothesis that lower-share neighborhoods tend to be slower, with a negative neighborhood-share coefficient in full model documentation.
- Optimized CatBoost currently achieves **MAE 8.001 days**, **RMSE 17.127 days**, **R² 0.258** on the held-out test split (log-scale: RMSE 0.944, R² 0.485), outperforming a median baseline (MAE 9.71 days, RMSE 20.77, R² -0.091 in saved output cells).

---

## Reproducibility

### Environment
Use the provided dependencies:

```bash
pip install -r requirements.txt
```

### Run order (recommended)
1. `01_citywide_data_preprocessing_eda.ipynb`
2. `02_council_district_eda.ipynb`
3. `03_catboost_modeling.ipynb`
4. `finding.ipynb`

### Notes
- Use deterministic sorting before rolling-window feature engineering.
- Keep time-based splits fixed for fair comparisons.
- For strict reproducibility, set fixed seeds and deterministic model settings.

---

## Repository Layout

```text
.
├── 01_citywide_data_preprocessing_eda.ipynb
├── 02_council_district_eda.ipynb
├── 03_catboost_modeling.ipynb
├── finding.ipynb
├── csv_files/
├── models/
├── district_trends_over_time.png
├── requirements.txt
└── README.md
```

---

## Methods Summary

- **Statistical analysis:** distributional comparisons, service-composition analysis, district/neighborhood aggregation.
- **Mixed-effects modeling:** cross-classified random effects (request type + neighborhood) with time controls.
- **ML modeling:** CatBoost regression with time-aware validation, baseline benchmarking, and Optuna tuning.

---

## Current Challenges and Next Steps

### 1) Mixed-effects sensitivity to sample restriction
**Challenge:**  
The neighborhood-share effect is statistically significant in the full mixed-effects specification, but becomes less significant in 90/95/99 category-mass sensitivity subsets.

**Why this happens:**  
Restricting to high-volume categories reduces variation and can weaken statistical power.

**My plan to address:**  
- Keep the full-spec model as the primary inferential model.  
- However, try different windows where I remove just neighborhood volume, and case volume separately
- Report restricted-sample models as robustness checks (direction + confidence intervals, not only p-values).  
- Prioritize coefficient stability across specifications over single-threshold significance.

---

### 2) Heavy right-tail response-time distribution
**Challenge:**  
`case_age_days` is strongly right-skewed with extreme long-delay cases, which increases prediction variance and makes tail cases hard to fit. Model can fit well against typical cases, but struggle to high-latency ones

**Current mitigation:**  
- Use `log1p(case_age_days)` for modeling.  
- Compare against baseline and tuned models on the same time-based split.

**Potential Approaches:**  
- Add a range-based target track (bucketed delays) as a complementary model:
  - example bins: `0–1`,`2–3` `4–7`, `8–21`, `22–90`, `>90` days 
  - These bins could still provide useful information
- Evaluate both:
  1. regression model (continuous delay), and  
  2. classification model (delay bucket),  
  then compare utility for policy and operational decisions.

- Or try modeling on just the top 10 most frequent cases first, and see if the model can generalize well
## Caveats

- Results are based on closed-case records and can reflect operational process changes over time.
- Some predictors may be operationally generated fields; validate feature availability-at-request-time when framing deployment claims.
- Outlier handling and cohort windows can affect effect sizes; sensitivity analyses are included in notebook workflow.

---

## Author

**Jaden Wu**  
UC San Diego, Data Science
