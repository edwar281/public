"""Disruption detector: county-quarters where speed tests surge while speeds fall.

People run speed tests when their connection is misbehaving, so a jump in test volume is itself a
signal. For each county with 30 complete quarters and at least 300 fixed-broadband tests in every
quarter, we take log(tests), log(median download) and log(median latency), remove the national
quarter effect (seasonality, the 2020 lockdown surge), then remove the county's own quadratic
trend. Residuals are scaled by the county's robust spread (1.4826 x MAD), giving a z-score
per county-quarter. A county-quarter is flagged when test volume is unusually high (z > 4) and
either download speed is unusually low (z < -2) or latency unusually high (z > 3).
"""
import json
import duckdb, numpy as np, pandas as pd

p = duckdb.sql("SELECT * FROM 'data/panel.parquet' WHERE kind = 'fixed'").df()
a = pd.read_parquet("data/county_attrs.parquet")
n_q = p.q.nunique()
cnt, mn = p.groupby("fips").q.count(), p.groupby("fips").tests.min()
keep = cnt[cnt == n_q].index.intersection(mn[mn >= 300].index)
d = p[p.fips.isin(keep)].copy()
d["t"] = d.q.str[:4].astype(int) + (d.q.str[-1].astype(int) - 1) / 4


def resid(col):
    y = np.log(d[col])
    y = y - y.groupby(d.q).transform("mean")
    out = pd.Series(index=d.index, dtype=float)
    for _, g in y.groupby(d.fips):
        t = d.loc[g.index, "t"]
        out[g.index] = g - np.polyval(np.polyfit(t, g, 2), t)
    return out


for col in ["tests", "med_d", "med_lat"]:
    r = resid(col)
    mad = r.groupby(d.fips).transform(lambda s: 1.4826 * np.median(np.abs(s - np.median(s))))
    d["z_" + col] = r / mad
d = d.merge(a[["fips", "state_abbr", "county_name", "pop_2020"]], on="fips", how="left")
d["flag"] = (d.z_tests > 4) & ((d.z_med_d < -2) | (d.z_med_lat > 3))
d["score"] = d.z_tests.clip(lower=0) + (-d.z_med_d).clip(lower=0) + d.z_med_lat.clip(lower=0)

# Known event used as an external check: Hurricane Helene, late Sep 2024, western North Carolina.
KNOWN = {("37021", "2024Q4"): "Hurricane Helene", ("37089", "2024Q4"): "Hurricane Helene"}
f = d[d.flag].sort_values("score", ascending=False)
f["event"] = [KNOWN.get((a_, b_), "") for a_, b_ in zip(f.fips, f.q)]
cols = ["fips", "q", "state_abbr", "county_name", "tests", "med_d", "med_lat", "z_tests", "z_med_d", "z_med_lat", "event"]
out = dict(n_counties=int(len(keep)), n_cells=int(len(d)), n_flagged=int(d.flag.sum()),
           flagged=f[cols].round(2).to_dict("records"),
           series={fp: d[d.fips == fp].sort_values("q")[["q", "tests", "med_d", "med_lat", "z_tests", "z_med_d", "z_med_lat"]].round(2).to_dict("list")
                   for fp in ["37021", "37089"]},
           z_hist=np.histogram(d.z_tests.clip(-8, 12), bins=np.arange(-8, 12.5, 0.5))[0].tolist())
json.dump(out, open("out/anomalies.json", "w"), indent=1)
print(out["n_counties"], "counties,", out["n_cells"], "county-quarters,", out["n_flagged"], "flagged")
print(f[cols].head(20).round(1).to_string())
