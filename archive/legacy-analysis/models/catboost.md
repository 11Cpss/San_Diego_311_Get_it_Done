## CatBoost Results Summary (Log-Target, Time-Based Split)

### Data Setup
- Total samples: **2,452,953**
- Features: **11** total
- Categorical: **3**
- Numerical: **8**
- Train period: **2016-05-20 to 2024-07-10**
- Test period: **2024-07-10 to 2025-12-31**

### Training Outcome
- Early stopping triggered after no improvement for 200 rounds.
- Best iteration: **313**
- Best validation RMSE (log-space): **1.1594**

### Model Performance
- **Training**
  - Log-space MAE: **0.893**
  - Log-space RMSE: **1.201**
  - Log-space R²: **0.497**
  - Day-space MAE: **30.13 days**
  - Day-space RMSE: **82.59 days**
- **Test**
  - Log-space MAE: **0.835**
  - Log-space RMSE: **1.159**
  - Log-space R²: **0.360**
  - Day-space MAE: **13.47 days**
  - Day-space RMSE: **35.13 days**

### Feature Importance (Top Drivers)
1. `service_name` (**34.71**)
2. `case_origin` (**29.49**)
3. `year` (**20.74**)

Lower-contribution features:
- `month` (**3.63**), `is_followup` (**3.24**), `comm_plan_name` (**2.72**)
- `is_infrastructure` (**1.81**), `hour` (**1.28**), `description_length` (**1.16**)
- `day_of_week` (**0.69**), `council_district` (**0.53**)

### Key Findings
- CatBoost improved over the Random Forest baseline:
  - Higher test Log-R² (**0.360 vs 0.338**)
  - Slightly lower test MAE/RMSE in days.
- Most predictive signal is concentrated in request type/process/time (`service_name`, `case_origin`, `year`).
- Long-tail error remains (RMSE still much larger than MAE), so rare/extreme wait times are still hard to predict.

### Conclusion
- CatBoost is currently the stronger model for this dataset.
- Next gains will likely come from better features (especially workload/backlog and temporal context), not model switching alone.

