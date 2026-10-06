"""National trends, the rural gap, county drivers, convergence, and the county map.

Two comparison windows are pooled over four quarters each to smooth seasonality:
  base   = 2019Q1-2019Q4
  latest = 2025Q3-2026Q2
County values in a window are test-weighted means of the quarterly county statistics.
"""
import json
import duckdb, numpy as np, pandas as pd
import statsmodels.formula.api as smf
import shapely
from shapely.geometry import shape

BASE = ["2019Q1", "2019Q2", "2019Q3", "2019Q4"]
LATEST = ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]
out = {}
nat = pd.read_parquet("data/national.parquet")
grp = pd.read_parquet("data/by_group.parquet").dropna(subset=["grp"])
out["national"] = {k: g.drop(columns="kind").round(4).to_dict("list") for k, g in nat.groupby("kind")}
out["groups"] = {f"{k}|{gname}": g.drop(columns=["kind", "grp"]).round(4).to_dict("list")
                 for (k, gname), g in grp.groupby(["kind", "grp"])}
out["totals"] = dict(tile_quarters=int(nat.tiles.sum()), tests=int(nat.tests.sum()),
                     quarters=int(nat.q.nunique()), first=nat.q.min(), last=nat.q.max())

p = pd.read_parquet("data/panel.parquet")
a = pd.read_parquet("data/county_attrs.parquet")


def window(qs, kind="fixed"):
    d = p[(p.kind == kind) & p.q.isin(qs)].copy()
    w = lambda c: (d[c] * d.tests).groupby(d.fips).sum() / d.tests.groupby(d.fips).sum()
    return pd.DataFrame({"tests": d.groupby("fips").tests.sum(), "med_d": w("med_d"), "med_u": w("med_u"),
                         "med_lat": w("med_lat"), "s100": w("share_100_20")}).reset_index()


b, l = window(BASE), window(LATEST)
c = l.merge(b, on="fips", suffixes=("", "_19")).merge(a, on="fips", how="left")
c = c[(c.tests >= 200) & (c.tests_19 >= 200)]
c["grp"] = pd.cut(c.rucc_2023, [0, 1, 3, 7, 9], labels=["Large metro", "Smaller metro", "Nonmetro town", "Rural"])

# Drivers of county speed today: rurality, income, density, state fixed effects -------------------
r = c.dropna(subset=["rucc_2023", "median_income", "pop_density"]).copy()
r = r[r.pop_density > 0]
r["ld"], r["linc"], r["lden"] = np.log(r.med_d), np.log(r.median_income), np.log(r.pop_density)
r["rucc"] = r.rucc_2023.astype(int).astype(str)
specs = {"rurality only": "ld ~ C(rucc)", "+ income": "ld ~ C(rucc) + linc",
         "+ income + state": "ld ~ C(rucc) + linc + C(state_abbr)",
         "+ income + density + state": "ld ~ C(rucc) + linc + lden + C(state_abbr)"}
reg = []
for name, fml in specs.items():
    m = smf.ols(fml, r).fit(cov_type="cluster", cov_kwds={"groups": r.state_abbr})
    row = dict(spec=name, n=int(m.nobs), r2=round(m.rsquared, 3))
    for k, lab in (("linc", "income"), ("lden", "density"), ("C(rucc)[T.9]", "rucc9")):
        if k in m.params:
            row[lab] = round(float(m.params[k]), 3); row[lab + "_se"] = round(float(m.bse[k]), 3)
    reg.append(row)
out["regression"] = reg
# Same for upgrade over time: did lower-income counties catch up?
r["growth"] = np.log(r.med_d / r.med_d_19)
r["ld19"] = np.log(r.med_d_19)
m = smf.ols("growth ~ ld19 + linc + C(rucc) + C(state_abbr)", r).fit(cov_type="cluster", cov_kwds={"groups": r.state_abbr})
out["convergence"] = dict(beta=round(float(m.params["ld19"]), 3), beta_se=round(float(m.bse["ld19"]), 3),
                          income=round(float(m.params["linc"]), 3), income_se=round(float(m.bse["linc"]), 3),
                          n=int(m.nobs))
# income quintile x group table for the latest window
r["inc_q"] = pd.qcut(r.median_income, 5, labels=[1, 2, 3, 4, 5]).astype(int)
iq = r.groupby(["grp", "inc_q"], observed=True).agg(med_d=("med_d", "median"), s100=("s100", "median"),
                                                     n=("fips", "count")).reset_index()
out["income_grid"] = iq.round(3).assign(grp=iq.grp.astype(str)).to_dict("records")
out["income_q_bounds"] = [round(float(x)) for x in r.median_income.quantile([.2, .4, .6, .8])]

# County scatter (latest vs 2019) for the dashboard
sc = c[["fips", "state_abbr", "county_name", "pop_2020", "med_d", "med_d_19",
                                       "s100", "s100_19", "median_income", "grp", "tests"]].copy()
sc["grp"] = sc.grp.astype(str).replace("nan", "Connecticut (unclassified)")
sc["median_income"] = sc.median_income.fillna(0)
out["counties"] = sc.round(3).to_dict("list")
lag = r[r.pop_2020 >= 10000].nsmallest(12, "s100")
out["laggards"] = lag[["state_abbr", "county_name", "pop_2020", "med_d", "s100", "med_d_19", "median_income"]].round(3).to_dict("records")

# Mobile: the 5G catch-up
mb, ml = window(BASE, "mobile"), window(LATEST, "mobile")
out["mobile_vs_fixed"] = dict(
    fixed_19=float(nat[(nat.kind == "fixed") & nat.q.isin(BASE)].med_d.mean()),
    fixed_26=float(nat[(nat.kind == "fixed") & nat.q.isin(LATEST)].med_d.mean()),
    mobile_19=float(nat[(nat.kind == "mobile") & nat.q.isin(BASE)].med_d.mean()),
    mobile_26=float(nat[(nat.kind == "mobile") & nat.q.isin(LATEST)].med_d.mean()))

# County map: Albers equal-area for the contiguous US, simplified, as SVG path strings -----------
gj = json.load(open("ref/geojson-counties-fips.json"))
lat0, lon0, p1, p2 = map(np.radians, (37.5, -96.0, 29.5, 45.5))
n_ = (np.sin(p1) + np.sin(p2)) / 2; C = np.cos(p1) ** 2 + 2 * n_ * np.sin(p1)
rho0 = np.sqrt(C - 2 * n_ * np.sin(lat0)) / n_


def albers(lon, lat):
    lon, lat = np.radians(lon), np.radians(lat)
    rho = np.sqrt(C - 2 * n_ * np.sin(lat)) / n_
    th = n_ * (lon - lon0)
    return rho * np.sin(th), rho0 - rho * np.cos(th)


paths = {}
for f in gj["features"]:
    if f["id"][:2] in ("02", "15", "72"):
        continue
    g = shape(f["geometry"]).simplify(0.015, preserve_topology=True)
    polys = getattr(g, "geoms", [g])
    d = []
    for poly in polys:
        xs, ys = np.asarray(poly.exterior.coords).T
        X, Y = albers(xs, ys)
        pts = np.round(np.c_[X, -Y] * 1000).astype(int)
        if len(pts) < 3:
            continue
        d.append("M" + "L".join(f"{x:g},{y:g}" for x, y in pts) + "Z")
    paths[f["id"].replace("46113", "46102")] = "".join(d)
out["map_paths"] = paths

json.dump(out, open("out/ookla.json", "w"), separators=(",", ":"))
print(pd.DataFrame(reg).to_string())
print(out["convergence"])
print(iq.pivot(index="grp", columns="inc_q", values="med_d").round(0))
print(pd.DataFrame(out["laggards"]).to_string())
print(out["mobile_vs_fixed"])
import os; print("json KB", os.path.getsize("out/ookla.json") // 1024)
