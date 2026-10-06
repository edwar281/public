"""Out-of-time default model and a profit-based approval policy.

Population: 36-month loans. Train on 2013-01..2014-09 issues, early-stop on 2014-10..12,
test on all 2015 issues (283K loans, all matured by the Mar-2019 snapshot).

Only fields known at application are used. Everything about the loan's later life
(payments, last FICO pull, hardship/settlement flags, recoveries) is excluded, and so are
bureau fields LC only began reporting in late 2015 (open_acc_6m, il_util, all_util, inq_fi...).

Three nested specs:
  grade_only  - LC's own sub-grade (1..35). This is the benchmark: LC's price ranking.
  borrower    - application + bureau attributes, no grade or rate.
  full        - borrower + sub-grade + interest rate.

Policy: from the investor's seat, each loan's realized profit is
  0.99 x borrower payments (1% servicing fee) + recoveries net of collection fees - funded amount.
We decline the riskiest k% of 2015 loans under each model's ranking and compare the profit kept.
"""
import json
import duckdb, numpy as np, pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, average_precision_score

NUM = ["loan_amnt", "fico_range_low", "dti", "annual_inc", "revol_util", "revol_bal", "open_acc",
       "total_acc", "inq_last_6mths", "delinq_2yrs", "pub_rec", "mort_acc", "acc_open_past_24mths",
       "bc_util", "tot_cur_bal", "total_rev_hi_lim", "num_tl_op_past_12m", "pct_tl_nvr_dlq",
       "percent_bc_gt_75", "pub_rec_bankruptcies", "mo_sin_rcnt_tl", "avg_cur_bal", "bc_open_to_buy",
       "mths_since_recent_inq", "num_actv_rev_tl", "tot_hi_cred_lim", "total_bc_limit",
       "total_il_high_credit_limit", "mo_sin_old_rev_tl_op", "mths_since_last_delinq"]
CAT = ["home_ownership", "verification_status", "purpose", "addr_state", "emp_length", "application_type"]

con = duckdb.connect()
df = con.sql(f"""
SELECT a.id::BIGINT id, l.iss, l.bad, l.sub_grade, l.grade, a.int_rate, a.installment, a.funded_amnt,
       0.99 * (a.total_pymnt - a.recoveries) + a.recoveries - a.collection_recovery_fee - a.funded_amnt profit,
       {", ".join("a." + c for c in NUM + CAT)},
       date_diff('month', strptime(a.earliest_cr_line, '%b-%Y'), l.iss) credit_age_m,
       a.installment * 12 / nullif(a.annual_inc, 0) pti
FROM 'data/accepted.parquet' a JOIN 'data/loans.parquet' l ON a.id::BIGINT = l.id
WHERE l.term_m = 36 AND l.iss BETWEEN '2013-01-01' AND '2015-12-31'""").df()
df["iss"] = pd.to_datetime(df.iss)
df["sg"] = df.sub_grade.map({f"{g}{i}": k * 5 + i for k, g in enumerate("ABCDEFG") for i in range(1, 6)})
for c in CAT:
    df[c] = df[c].astype("category")
NUM += ["credit_age_m", "pti"]
tr = df[df.iss < "2014-10-01"]
va = df[(df.iss >= "2014-10-01") & (df.iss < "2015-01-01")]
te = df[df.iss >= "2015-01-01"].copy()
print(f"train {len(tr):,}  valid {len(va):,}  test {len(te):,}  test bad rate {te.bad.mean():.3f}")

SPECS = {"grade_only": ["sg"], "borrower": NUM + CAT, "full": NUM + CAT + ["sg", "int_rate"]}
P = dict(objective="binary", learning_rate=0.03, num_leaves=63, min_data_in_leaf=400,
         feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10, verbose=-1, seed=7)
out, models = {"n": dict(train=len(tr), valid=len(va), test=len(te), test_bad=round(float(te.bad.mean()), 4),
                         test_usd=float(te.funded_amnt.sum()))}, {}
for name, feats in SPECS.items():
    if name == "grade_only":
        # Empirical bad rate per sub-grade on the training window: LC's ranking, calibrated.
        rate = tr.groupby("sg").bad.mean()
        te[f"p_{name}"] = te.sg.map(rate).fillna(tr.bad.mean())
    else:
        m = lgb.train(P, lgb.Dataset(tr[feats], tr.bad.astype(int)), 3000,
                      valid_sets=[lgb.Dataset(va[feats], va.bad.astype(int))],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        models[name] = (m, feats)
        te[f"p_{name}"] = m.predict(te[feats], num_iteration=m.best_iteration)
    y, p = te.bad.astype(int), te[f"p_{name}"]
    out[name] = dict(auc=round(roc_auc_score(y, p), 4), pr_auc=round(average_precision_score(y, p), 4))
    print(name, out[name])

# Decile lift and calibration of the full model on the test year
te["dec"] = pd.qcut(te.p_full.rank(method="first"), 10, labels=False)
dec = te.groupby("dec").agg(pred=("p_full", "mean"), actual=("bad", "mean"), rate=("int_rate", "mean"),
                            profit=("profit", "sum"), usd=("funded_amnt", "sum")).reset_index()
dec["roi"] = dec.profit / dec.usd
out["deciles"] = dec.round(4).to_dict("records")

# Risk hidden inside a grade: split each grade into model-PD quintiles
te["q_in_grade"] = te.groupby("grade").p_full.transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
wg = te[te.grade.isin(list("ABCDE"))].groupby(["grade", "q_in_grade"]).agg(
    bad=("bad", "mean"), rate=("int_rate", "mean"), roi=("profit", "sum"), usd=("funded_amnt", "sum")).reset_index()
wg["roi"] = wg.roi / wg.usd
out["within_grade"] = wg.drop(columns="usd").round(4).to_dict("records")

# Approval policy: decline the riskiest k% under each ranking
base = te.profit.sum()
curve = []
for k in np.arange(0, 41, 1):
    row = {"k": int(k)}
    for name in SPECS:
        cut = te[f"p_{name}"].quantile(1 - k / 100) if k else np.inf
        keep = te[f"p_{name}"] <= cut if k else np.ones(len(te), bool)
        if name == "grade_only" and k:
            # ties within a sub-grade: decline at random within the marginal sub-grade
            order = te.sample(frac=1, random_state=1).sort_values(f"p_{name}", kind="stable").index
            keep = te.index.isin(order[: int(round(len(te) * (1 - k / 100)))])
        row[name] = float(te.profit[keep].sum())
    curve.append(row)
out["policy"] = dict(base_profit=float(base), base_roi=round(float(base / te.funded_amnt.sum()), 4), curve=curve)
best = {n: max(curve, key=lambda r: r[n]) for n in SPECS}
out["policy"]["best"] = {n: dict(k=b["k"], profit=b[n], uplift=b[n] - base) for n, b in best.items()}
print(json.dumps(out["policy"]["best"], indent=1), f"base {base/1e6:.1f}M")

# Feature attribution for the full model: mean |SHAP| via LightGBM's built-in contributions
m, feats = models["full"]
samp = te.sample(20000, random_state=3)
contrib = m.predict(samp[feats], num_iteration=m.best_iteration, pred_contrib=True)[:, :-1]
imp = pd.Series(np.abs(contrib).mean(0), index=feats).sort_values(ascending=False)
out["shap_top"] = [dict(feature=f, mean_abs=round(float(v), 4)) for f, v in imp.head(15).items()]
m2, f2 = models["borrower"]
contrib = m2.predict(samp[f2], num_iteration=m2.best_iteration, pred_contrib=True)[:, :-1]
imp2 = pd.Series(np.abs(contrib).mean(0), index=f2).sort_values(ascending=False)
out["shap_borrower"] = [dict(feature=f, mean_abs=round(float(v), 4)) for f, v in imp2.head(12).items()]
print(imp.head(12).round(3).to_string())
json.dump(out, open("out/model.json", "w"), indent=1)
