"""Clean loan table, vintage loss curves, realized pool returns by grade, and the application funnel.

Snapshot: the file runs through loans issued Dec-2018 with payments through Mar-2019. A 36-month
loan issued by Mar-2016 has therefore reached maturity; the "matured book" used for realized
returns is 36-month loans issued 2010-2015 (~610K loans), where 99.95% have a final status.

Outcome: bad = Charged Off / Default / Late 31-120 days (LC charges off at 150 days past due).
Charge-off timing is not in the file; we place it 5 months after the last payment received
(the 150-day delinquency clock), or 5 months after issue for loans that never paid.
"""
import json
import duckdb, numpy as np, pandas as pd
from scipy.optimize import brentq

SNAP = pd.Timestamp("2019-03-01")
con = duckdb.connect()
con.sql("SET preserve_insertion_order=false")
con.sql("""
CREATE TABLE loans AS
SELECT id::BIGINT id, strptime(issue_d, '%b-%Y')::DATE iss, trim(term) term,
       CAST(regexp_extract(term, '\\d+') AS INT) term_m,
       grade, sub_grade, int_rate, installment, loan_amnt, funded_amnt, purpose, addr_state,
       fico_range_low, dti, annual_inc,
       replace(loan_status, 'Does not meet the credit policy. Status:', '') status,
       replace(loan_status, 'Does not meet the credit policy. Status:', '')
           IN ('Charged Off', 'Default', 'Late (31-120 days)') bad,
       replace(loan_status, 'Does not meet the credit policy. Status:', '')
           IN ('Fully Paid', 'Charged Off', 'Default') final,
       strptime(last_pymnt_d, '%b-%Y')::DATE last_pay, total_pymnt, recoveries, collection_recovery_fee
FROM 'data/accepted.parquet'""")
con.sql("""ALTER TABLE loans ADD COLUMN pay_m INT""")
con.sql("""UPDATE loans SET pay_m = coalesce(date_diff('month', iss, last_pay), 0)""")
con.sql("""ALTER TABLE loans ADD COLUMN co_m INT""")
con.sql("""UPDATE loans SET co_m = CASE WHEN bad THEN pay_m + 5 END""")
con.sql("COPY loans TO 'data/loans.parquet' (FORMAT parquet)")
out = {}

# 1. Volume and the application funnel ---------------------------------------------------------
vol = con.sql("""SELECT year(iss) y, count(*) n, sum(funded_amnt) usd, avg(int_rate) rate,
                        avg((term_m = 60)::INT) share_60m FROM loans GROUP BY 1 ORDER BY 1""").df()
rej = con.sql("""SELECT year(app_date) y, count(*) n FROM 'data/rejected.parquet' GROUP BY 1""").df()
f = vol.merge(rej, on="y", suffixes=("_acc", "_rej"))
f["approval_rate"] = f.n_acc / (f.n_acc + f.n_rej)
out["funnel"] = f.round(4).to_dict("records")
out["totals"] = dict(loans=int(vol.n.sum()), usd=float(vol.usd.sum()), rejected=int(rej.n.sum()))

# 2. Vintage curves: cumulative $ charged off by months on book, 36-month loans ----------------
#    At each month m, a loan counts only if iss + m <= snapshot (fully observed to m).
L = con.sql("SELECT year(iss) y, iss, funded_amnt f, bad, co_m FROM loans WHERE term_m = 36").df()
L["obs"] = ((SNAP.year - pd.to_datetime(L.iss).dt.year) * 12 + SNAP.month - pd.to_datetime(L.iss).dt.month)
curves = {}
for y in range(2012, 2019):
    d = L[L.y == y]
    pts = []
    for m in range(0, 43, 3):
        e = d[d.obs >= m]
        if len(e) < 0.9 * len(d):
            break
        pts.append([m, round(float((e.f * (e.bad & (e.co_m <= m))).sum() / e.f.sum()), 5)])
    curves[str(y)] = pts
out["vintage_curves"] = curves

# 3. Realized pool returns on the matured book ---------------------------------------------------
#    Reconstruct monthly cash flows per loan: scheduled installments until the last payment month,
#    scaled so the total equals what was actually received; any excess in the last month is a prepayment.
#    Recoveries (net of collection fees) arrive 6 months after charge-off. Investors also pay LC's
#    1% servicing fee on borrower payments. Pool IRR solves sum CF_t / (1+r)^t = funded.
M = con.sql("""SELECT grade, sub_grade, year(iss) y, int_rate, installment, funded_amnt f, bad,
                      greatest(pay_m, 1) pay_m, total_pymnt - recoveries borrower_paid,
                      recoveries - collection_recovery_fee net_rec, co_m
               FROM loans WHERE term_m = 36 AND year(iss) BETWEEN 2010 AND 2015 AND final""").df()
H = 50


def pool_cf(d):
    cf = np.zeros(H)
    m = d.pay_m.clip(upper=45).to_numpy()
    inst, paid = d.installment.to_numpy(), d.borrower_paid.to_numpy() * 0.99
    reg = np.minimum(inst * m, paid)            # regular installments actually covered
    per = reg / m
    np.add.at(cf, m, paid - reg)                # prepayment lump in last month
    # spread regular installments over months 1..m via a difference array
    diff = np.zeros(H + 1)
    np.add.at(diff, np.ones_like(m), per)
    np.add.at(diff, m + 1, -per)
    cf += np.cumsum(diff)[:H]
    rec = d.net_rec.to_numpy()
    np.add.at(cf, np.minimum(d.co_m.fillna(0).astype(int).to_numpy() + 6, H - 1), rec)
    return cf, d.f.sum()


def irr(cf, f):
    t = np.arange(H)
    g = lambda r: (cf / (1 + r) ** t).sum() - f
    return (1 + brentq(g, -0.5, 0.5)) ** 12 - 1


def summarize(d):
    cf, f = pool_cf(d)
    return dict(n=len(d), usd=float(f), rate=round(float(np.average(d.int_rate, weights=d.f)), 3),
                bad_rate=round(float(d.bad.mean()), 4),
                loss_rate=round(float(((d.f - d.borrower_paid - d.net_rec).clip(lower=0) * d.bad).sum() / f), 4),
                irr=round(100 * irr(cf, f), 2), multiple=round(float(cf.sum() / f), 4))


out["by_grade"] = [dict(grade=g, **summarize(d)) for g, d in M.groupby("grade")]
out["by_subgrade"] = [dict(sub_grade=g, **summarize(d)) for g, d in M.groupby("sub_grade") if len(d) >= 500]
out["by_year_grade"] = [dict(y=int(y), grade=g, **summarize(d)) for (y, g), d in M.groupby(["y", "grade"]) if len(d) >= 300]
out["matured_book"] = summarize(M)
json.dump(out, open("out/loans.json", "w"), indent=1)
print(pd.DataFrame(out["by_grade"]).to_string())
print(out["matured_book"])
print(f.to_string())
