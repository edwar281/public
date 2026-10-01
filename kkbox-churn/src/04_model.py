"""
04_model.py — Churn propensity model with out-of-time validation and calibration.

  Train:  Feb-2017 expiring members (85% fit / 15% in-time holdout for early stopping + calibration)
  Test:   Mar-2017 expiring members (out-of-time; never touched during fitting)

Compares three specifications to show where the signal comes from:
  A. Profile only (demographics + signup channel)
  B. + Billing / transaction history
  C. + Daily listening behaviour (400M usage rows)   <- final model
Then: calibration (in-time vs out-of-time, before/after recalibration), decile lift, SHAP drivers.
Writes out/model.json.
"""
import json, os, numpy as np, pandas as pd, lightgbm as lgb, shap
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

FEAT = os.environ.get("KKBOX_FEAT", "data/features")
OUT = os.environ.get("KKBOX_OUT", "out")
feb = pd.read_parquet(f"{FEAT}/feb.parquet").sort_values("uid", ignore_index=True)  # fixed order -> reproducible split
mar = pd.read_parquet(f"{FEAT}/mar.parquet").sort_values("uid", ignore_index=True)

CAT = ["city", "registered_via", "last_pm"]
PROFILE = ["city", "age", "is_male", "registered_via", "days_since_registration"]
TXN = ["n_txn", "n_cancel", "n_pay_methods", "n_plan_lengths", "days_since_first_txn", "total_paid", "n_txn_90d",
       "n_cancel_90d", "share_auto_renew", "share_discounted", "days_since_cancel", "n_prior_lapses",
       "last_is_cancel", "last_auto_renew", "last_pm", "days_to_expiry", "last_plan_days", "last_list_price",
       "last_paid", "last_discount", "last_price_per_day", "days_since_last_sub"]
USE = [c for c in feb.columns if c.startswith(("days_active_", "hours_", "uniq_songs_"))] + \
      ["skip_share_30d", "complete_share_30d", "days_since_last_active", "usage_trend_7v30", "usage_trend_30v90"]
SPECS = {"A. Profile only": PROFILE, "B. + Billing history": PROFILE + TXN, "C. + Listening behaviour": PROFILE + TXN + USE}

for d in (feb, mar):
    for c in CAT:
        d[c] = d[c].fillna(-1).astype(int).astype("category")
    d["no_usage_180d"] = d["hours_180d"].isna().astype(int)
cats = pd.api.types.union_categoricals
for c in CAT:  # align category levels between cohorts
    levels = sorted(set(feb[c].cat.categories) | set(mar[c].cat.categories))
    feb[c] = feb[c].cat.set_categories(levels); mar[c] = mar[c].cat.set_categories(levels)
SPECS["C. + Listening behaviour"] = SPECS["C. + Listening behaviour"] + ["no_usage_180d"]

rng = np.random.default_rng(42)
hold = rng.random(len(feb)) < 0.15
fit_df, hold_df = feb[~hold], feb[hold]
y_fit, y_hold, y_mar = fit_df.is_churn.values, hold_df.is_churn.values, mar.is_churn.values

PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=127, min_child_samples=200,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
              verbose=-1, num_threads=2, seed=42)


def metrics(y, p):
    return {"auc": round(roc_auc_score(y, p), 4), "pr_auc": round(average_precision_score(y, p), 4),
            "log_loss": round(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6)), 4), "brier": round(brier_score_loss(y, p), 4),
            "mean_pred": round(float(np.mean(p)), 4), "actual_rate": round(float(np.mean(y)), 4)}


res = {"cohorts": {"train_feb": {"n": int(len(feb)), "churn_rate": round(float(feb.is_churn.mean()), 4)},
                   "test_mar": {"n": int(len(mar)), "churn_rate": round(float(mar.is_churn.mean()), 4)}},
       "specs": {}}
models = {}
for name, cols in SPECS.items():
    dtr = lgb.Dataset(fit_df[cols], y_fit, categorical_feature=[c for c in CAT if c in cols])
    dva = lgb.Dataset(hold_df[cols], y_hold, reference=dtr)
    m = lgb.train(PARAMS, dtr, 3000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
    models[name] = (m, cols)
    res["specs"][name] = {"n_features": len(cols), "trees": m.best_iteration,
                          "holdout_feb": metrics(y_hold, m.predict(hold_df[cols], num_iteration=m.best_iteration)),
                          "oot_mar": metrics(y_mar, m.predict(mar[cols], num_iteration=m.best_iteration))}
    print(name, res["specs"][name], flush=True)

final, cols = models["C. + Listening behaviour"]
p_hold = final.predict(hold_df[cols], num_iteration=final.best_iteration)
p_mar = final.predict(mar[cols], num_iteration=final.best_iteration)

# ---------- calibration ----------
iso = IsotonicRegression(out_of_bounds="clip").fit(p_hold, y_hold)       # in-time recalibration


def logit(p): p = np.clip(p, 1e-6, 1 - 1e-6); return np.log(p / (1 - p))
def expit(z): return 1 / (1 + np.exp(-z))


# Out-of-time drift fix: re-estimate only the intercept (prior shift) on a small labelled slice of the new
# month (5% of March), then evaluate on the other 95%. This mirrors a monthly production recalibration.
mask5 = rng.random(len(mar)) < 0.05
lr = LogisticRegression(C=1e6).fit(logit(p_mar[mask5]).reshape(-1, 1), y_mar[mask5])
p_mar_recal = expit(lr.intercept_[0] + lr.coef_[0, 0] * logit(p_mar))
ev = ~mask5


def reliability(y, p, bins=10):
    q = np.quantile(p, np.linspace(0, 1, bins + 1)); q[0], q[-1] = -1, 2
    b = np.digitize(p, q[1:-1])
    return [{"pred": round(float(p[b == i].mean()), 4), "obs": round(float(y[b == i].mean()), 4), "n": int((b == i).sum())}
            for i in range(bins)]


res["calibration"] = {
    "holdout_feb_raw": reliability(y_hold, p_hold),
    "oot_mar_raw": reliability(y_mar[ev], p_mar[ev]),
    "oot_mar_recal": reliability(y_mar[ev], p_mar_recal[ev]),
    "metrics": {"oot_mar_raw": metrics(y_mar[ev], p_mar[ev]), "oot_mar_recal": metrics(y_mar[ev], p_mar_recal[ev]),
                "recal_slope": round(float(lr.coef_[0, 0]), 3), "recal_intercept": round(float(lr.intercept_[0]), 3)}}

# ---------- decile lift (out-of-time) ----------
order = np.argsort(-p_mar)
dec = np.empty(len(p_mar), int); dec[order] = np.arange(len(p_mar)) * 10 // len(p_mar)
tot = y_mar.sum()
lift = []
for k in range(10):
    s = dec == k
    lift.append({"decile": k + 1, "n": int(s.sum()), "churn_rate": round(float(y_mar[s].mean()), 4),
                 "lift": round(float(y_mar[s].mean() / y_mar.mean()), 2),
                 "cum_capture": round(float(y_mar[dec <= k].sum() / tot), 4)})
res["lift"] = lift
# capture at fine-grained targeting depths
res["capture_at"] = {f"{pct}%": round(float(y_mar[order[: int(len(order) * pct / 100)]].sum() / tot), 4)
                     for pct in (1, 2, 5, 10, 20, 30)}

# ---------- SHAP drivers (out-of-time sample) ----------
samp = mar.sample(20000, random_state=1)
explainer = shap.TreeExplainer(final)
sv = explainer.shap_values(samp[cols])
sv = sv[1] if isinstance(sv, list) else sv
imp = pd.Series(np.abs(sv).mean(0), index=cols).sort_values(ascending=False)
res["shap_top"] = [{"feature": f, "mean_abs_shap": round(float(v), 4)} for f, v in imp.head(15).items()]


def dependence(feat, bins):
    x = samp[feat].astype(float).values; s = sv[:, cols.index(feat)]
    out = []
    for lo, hi, label in bins:
        m = (x >= lo) & (x < hi)
        if m.sum() >= 100:
            out.append({"bin": label, "mean_shap": round(float(s[m].mean()), 4), "n": int(m.sum()),
                        "churn_rate": round(float(samp.is_churn.values[m].mean()), 4)})
    return out


res["dependence"] = {
    "days_to_expiry": dependence("days_to_expiry", [(-9999, 0, "Expired"), (0, 15, "0-14"), (15, 30, "15-29"),
                                                    (30, 45, "30-44"), (45, 60, "45-59"), (60, 1e9, "60+")]),
    "days_since_last_active": dependence("days_since_last_active", [(0, 1, "0"), (1, 3, "1-2"), (3, 7, "3-6"),
                                                                    (7, 14, "7-13"), (14, 30, "14-29"), (30, 1e9, "30+")]),
    "last_auto_renew": dependence("last_auto_renew", [(0, 0.5, "Manual"), (0.5, 2, "Auto-renew")]),
}
json.dump(res, open(f"{OUT}/model.json", "w"))
final.save_model(f"{OUT}/lgbm_final.txt")
print(json.dumps({k: res[k] for k in ["calibration", "lift", "capture_at", "shap_top"]}, indent=0)[:6000])
