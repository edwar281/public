"""
02_survival.py — Survival / hazard analysis of new paying subscribers (time to first churn).

  * Kaplan-Meier survival with Greenwood 95% CIs, overall and by acquisition segment
  * Discrete monthly hazard (probability of churning in tenure month m given survival to m)
  * Cox proportional-hazards model (stratified by acquisition quarter) -> hazard ratios with 95% CIs
  * Trial funnel: share of free-trial starters that convert to a paid plan

Writes out/survival.json for the dashboard.
"""
import duckdb, json, os, numpy as np, pandas as pd
import statsmodels.api as sm
from statsmodels.duration.hazard_regression import PHReg

DB = os.environ.get("KKBOX_DB", "data/kkbox.duckdb")
OUT = os.environ.get("KKBOX_OUT", "out")
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect(DB, read_only=True)
df = con.execute("SELECT * FROM new_subs ORDER BY uid").fetchdf()   # fixed order -> reproducible Cox sample
df["event"] = df["event"].astype(int)
GRID = np.arange(0, 541, 30)          # day 0 .. ~18 months, monthly points


def km(d, e, grid=GRID):
    """Kaplan-Meier S(t) on a grid with Greenwood log-log 95% CI."""
    d, e = np.asarray(d), np.asarray(e)
    t, inv = np.unique(d, return_inverse=True)
    deaths = np.bincount(inv, weights=e)
    removed = np.bincount(inv)
    at_risk = len(d) - np.concatenate([[0], np.cumsum(removed)[:-1]])
    h = deaths / at_risk
    S = np.cumprod(1 - h)
    gw = np.cumsum(np.where(at_risk > deaths, deaths / (at_risk * (at_risk - deaths)), 0))
    idx = np.searchsorted(t, grid, side="right") - 1
    s = np.where(idx >= 0, S[np.clip(idx, 0, None)], 1.0)
    v = np.where(idx >= 0, gw[np.clip(idx, 0, None)], 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ll = np.log(-np.log(s)); se = np.sqrt(v) / np.abs(np.log(s))
        lo = np.exp(-np.exp(ll + 1.96 * se)); hi = np.exp(-np.exp(ll - 1.96 * se))
    lo = np.where(np.isfinite(lo), lo, s); hi = np.where(np.isfinite(hi), hi, s)
    n_risk = np.array([(d >= g).sum() for g in grid])
    keep = n_risk >= 200                                # don't report the noisy tail
    return {"t": grid[keep].tolist(), "s": np.round(s[keep], 4).tolist(),
            "lo": np.round(lo[keep], 4).tolist(), "hi": np.round(hi[keep], 4).tolist(),
            "n": int(len(d)), "events": int(e.sum())}


def monthly_hazard(sub):
    rows = []
    for m in range(12):
        a, b = 30 * m, 30 * (m + 1)
        at_risk = (sub.duration >= a).sum()
        ev = ((sub.duration >= a) & (sub.duration < b) & (sub.event == 1)).sum()
        rows.append(round(ev / at_risk, 4) if at_risk else None)
    return rows


# ---------- segment definitions ----------
df["plan"] = np.select([df.plan_days.between(28, 31), df.plan_days >= 90], ["Monthly", "Prepaid 90d+"], "Other")
df["billing"] = np.where(df.auto_renew == 1, "Auto-renew", "Manual renewal")
df["origin"] = np.where(df.from_trial == 1, "Converted from free trial", "Direct paid start")
df["price"] = np.where(df.discounted_start == 1, "Discounted first payment", "Full price")
df["age_band"] = pd.cut(df.age, [12, 24, 34, 44, 80], labels=["13-24", "25-34", "35-44", "45+"]).astype(str)
df.loc[df.age.isna(), "age_band"] = "Unknown"

res = {"cohort": {"n": int(len(df)), "churned": int(df.event.sum()),
                  "start": str(df.start_date.min().date()), "end": str(df.start_date.max().date())},
       "overall": km(df.duration, df.event), "segments": {}, "hazard": {}}

for seg in ["billing", "plan", "origin", "price", "age_band"]:
    res["segments"][seg] = {lvl: km(g.duration, g.event) for lvl, g in df.groupby(seg) if len(g) >= 2000}

res["hazard"]["overall"] = monthly_hazard(df)
for lvl, g in df.groupby("billing"):
    res["hazard"][lvl] = monthly_hazard(g)

# median survival & 12-month retention per segment
def s_at(curve, day):
    t = np.array(curve["t"]); s = np.array(curve["s"])
    return float(s[t <= day][-1]) if (t <= day).any() else None
res["retention_12m"] = {seg: {lvl: s_at(c, 360) for lvl, c in d.items()} for seg, d in res["segments"].items()}
res["retention_12m"]["overall"] = s_at(res["overall"], 360)

# ---------- Cox PH, stratified by acquisition quarter ----------
rng = np.random.default_rng(7)
s = df.sample(n=min(200_000, len(df)), random_state=7).copy()
s["quarter"] = pd.to_datetime(s.cohort_month).dt.to_period("Q").astype(str)
X = pd.DataFrame({
    "Auto-renew billing": s.auto_renew,
    "Prepaid 90d+ plan": (s.plan == "Prepaid 90d+").astype(int),
    "Converted from free trial": s.from_trial,
    "Discounted first payment": s.discounted_start,
    "Age 13-24 (vs 25-34)": (s.age_band == "13-24").astype(int),
    "Age 35-44 (vs 25-34)": (s.age_band == "35-44").astype(int),
    "Age 45+ (vs 25-34)": (s.age_band == "45+").astype(int),
    "Age unknown (vs 25-34)": (s.age_band == "Unknown").astype(int),
    "Gender reported": s.gender.notna().astype(int),
})
for rv in [7, 9, 3]:                                    # registration channel (ref = all others)
    X[f"Signup channel {rv}"] = (s.registered_via == rv).astype(int)
dur = s.duration.clip(lower=0.5).values
model = PHReg(dur, X.values.astype(float), status=s.event.values, strata=s.quarter.values, ties="breslow")
fit = model.fit()
ci = fit.conf_int()
res["cox"] = {"n": int(len(s)), "events": int(s.event.sum()), "terms": [
    {"term": c, "hr": round(float(np.exp(fit.params[i])), 3),
     "lo": round(float(np.exp(ci[i, 0])), 3), "hi": round(float(np.exp(ci[i, 1])), 3),
     "p": float(fit.pvalues[i])} for i, c in enumerate(X.columns)]}

# PH diagnostic for the biggest effect: hazard ratio auto-renew vs manual by tenure month
h_ar, h_m = np.array(res["hazard"]["Auto-renew"], float), np.array(res["hazard"]["Manual renewal"], float)
res["ph_check_auto_renew_hr_by_month"] = np.round(h_ar / h_m, 3).tolist()

# ---------- free-trial funnel ----------
trial = con.execute("""
WITH f AS (SELECT uid, min(tdate) d FROM txn WHERE cancel=0 AND paid=0 AND plan_days BETWEEN 7 AND 14
           AND tdate BETWEEN DATE '2015-03-01' AND DATE '2016-12-31' GROUP BY uid),
p AS (SELECT t.uid, min(t.tdate) pd FROM txn t JOIN f USING (uid) WHERE t.cancel=0 AND t.paid>0 AND t.tdate >= f.d GROUP BY t.uid)
SELECT count(*) trials, count(p.uid) FILTER (WHERE date_diff('day', d, pd) <= 45) converted_45d
FROM f LEFT JOIN p USING (uid)""").fetchone()
res["trial_funnel"] = {"trials": int(trial[0]), "converted_45d": int(trial[1]), "rate": round(trial[1] / trial[0], 4)}

json.dump(res, open(f"{OUT}/survival.json", "w"))
print(json.dumps({k: res[k] for k in ["cohort", "retention_12m", "trial_funnel"]}, indent=1))
print(pd.DataFrame(res["cox"]["terms"]).to_string(index=False))
print("PH check:", res["ph_check_auto_renew_hr_by_month"])
print("hazard:", res["hazard"])
