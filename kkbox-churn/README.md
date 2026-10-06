# Subscription Churn at Scale

Survival analysis and an out-of-time churn model on the public **WSDM-KKBox Churn Prediction** data:
6.8M member profiles, 23M billing transactions and 392M rows of daily usage logs.

**Author:** Will Edwards, PhD. Assisted by Claude.

**[Read the analysis →](https://edwar281.github.io/public/kkbox-churn/dashboard/)**  
(Or open `dashboard/index.html` locally; it is self-contained.)

## Headline results

| | |
|---|---|
| Out-of-time test (March 2017, 971K subscribers) | AUC 0.869 · PR-AUC 0.596 |
| Riskiest 10% of subscribers | Contains 60% of next month's churners (6.0× lift) |
| 12-month retention, new paid subscribers | 72% on auto-renew vs 37% on manual renewal |
| Auto-renew, adjusted (Cox PH) | Hazard ratio 0.45 (95% CI 0.44–0.46) |
| Free-trial conversion within 45 days | 10%. Converted trials churn 1.7× faster than direct sign-ups |
| Value of 392M usage rows over billing data | +0.004 AUC |
| Calibration drift Feb → Mar | Base rate 6.4% → 9.0%. Intercept/slope refit on a 5% labelled slice fixes the mean |

## Pipeline

```
make data        # scripts/download.sh: Kaggle API download (~9 GB); accept competition rules first
make parquet     # src/00a_to_parquet.py + src/00b_logs_to_parquet.py: 30 GB CSV -> 4.4 GB Parquet, streamed
make dashboard   # 01_spells -> 02_survival -> 03_features -> 04_model -> 05_build_dashboard
```

| Step | What it does |
|---|---|
| `01_spells.py` | Orders transactions and applies the official churn rule: no new paid subscription within 30 days of effective expiry, with cancellations pulling expiry forward. Builds the new-paid-subscriber cohort (Mar 2015–Dec 2016) with left-truncation guards. |
| `02_survival.py` | Kaplan–Meier with Greenwood log-log CIs, monthly discrete hazards, and Cox PH stratified by acquisition quarter (200K sample). Also a proportional-hazards check and the trial funnel. |
| `03_features.py` | Point-in-time features. Every input is dated on or before the cutoff: 2017-01-31 for the Feb cohort, 2017-02-28 for the Mar cohort. Covers billing history, prior lapses re-derived as of the cutoff, and 7/14/30/90/180-day usage windows. |
| `04_model.py` | LightGBM. Three nested specs (profile → +billing → +usage), out-of-time test, reliability curves, recalibration, decile lift and SHAP. |
| `05_build_dashboard.py` | Injects results into `dashboard/template.html` and writes `dashboard/index.html`, a self-contained page with no dependencies. |

Runs on 2 CPU cores and 8 GB RAM. Peak disk use is about 45 GB, including the raw archives.

## Design notes

- **Out-of-time, not random, validation.** The model is trained on February expirations and scored on March, the way it would run in production.
- **Leakage controls.** Features stop at the cutoff, before the label month. Lapse history is recomputed from pre-cutoff rows only.
- **Censoring.** Subscribers still active at the end of the data are censored, not counted as retained. A lapse is only counted when its 30-day window has fully elapsed.
- **Non-proportional hazards.** Manual-renewal risk spikes at first expiry and again at the 180-day mark. The Cox hazard ratio is reported as an average effect, alongside the month-by-month hazards.
- **Associations, not causal effects.** Auto-renew enrollment is self-selected.

## Data

WSDM – KKBox's Churn Prediction Challenge, Kaggle (KKBox, 2017). The data is not redistributed here; download it with your own Kaggle account after accepting the competition rules.
