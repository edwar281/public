"""Assign every US tile to a county and build the county attribute table.

Tile centroid -> county by point-in-polygon (shapely STRtree) against the Census
county boundaries in ref/geojson-counties-fips.json. Tiles that fall outside every
county (Canada/Mexico spill-over from the bounding boxes, open water) are dropped.

County attributes come from ref/county_rurality.csv: 2023 USDA Rural-Urban
Continuum Codes, 2020 population, land area and 2018-22 ACS median household income.
The boundary file predates three changes, handled here:
  * 46113 Shannon County SD was renamed 46102 Oglala Lakota.
  * 02261 Valdez-Cordova AK was split into 02063 + 02066; we merge the two back.
  * Connecticut replaced its 8 counties with 9 planning regions in 2022. They don't
    nest, so Connecticut tiles keep their old county and get no rurality/income attributes.
"""
import glob, json
import duckdb, numpy as np, pandas as pd
import shapely
from shapely.geometry import shape

con = duckdb.connect()
tiles = con.sql("""select quadkey, any_value(lon) lon, any_value(lat) lat
                   from read_parquet('data/us/*.parquet') group by quadkey""").df()
print(f"unique US-box tiles: {len(tiles):,}")

gj = json.load(open("ref/geojson-counties-fips.json"))
fips = np.array([f["id"] for f in gj["features"]])
polys = [shape(f["geometry"]) for f in gj["features"]]
tree = shapely.STRtree(polys)
pts = shapely.points(tiles.lon.values, tiles.lat.values)
pi, ci = tree.query(pts, predicate="within")
county = np.full(len(tiles), None, object)
county[pi] = fips[ci]
tiles["fips"] = county
tiles = tiles.dropna(subset=["fips"])
tiles["fips"] = tiles.fips.replace({"46113": "46102"})
print(f"tiles inside a US county: {len(tiles):,}")
tiles[["quadkey", "fips"]].to_parquet("data/quadkey_county.parquet", index=False)

# County attributes -----------------------------------------------------------
r = pd.read_csv("ref/county_rurality.csv", dtype={"fips": str}, encoding="latin-1")
vc = r[r.fips.isin(["02063", "02066"])]
merged = vc.iloc[[0]].copy()
merged["fips"], merged["county_name"] = "02261", "Valdez-Cordova Census Area"
for c in ["pop_2020", "acs_pop", "land_area_sqmi"]:
    merged[c] = vc[c].sum()
merged["pop_density"] = merged.acs_pop / merged.land_area_sqmi
merged["median_income"] = np.average(vc.median_income, weights=vc.acs_pop)
r = pd.concat([r[~r.fips.isin(["02063", "02066"])], merged])
attrs = r[["fips", "state_abbr", "county_name", "pop_2020", "land_area_sqmi", "pop_density",
           "rucc_2023", "omb_designation", "median_income"]].copy()
names = {f["id"]: f["properties"].get("NAME") for f in gj["features"]}
ct = pd.DataFrame({"fips": [f for f in fips if f.startswith("09")]})
ct["state_abbr"], ct["county_name"] = "CT", ct.fips.map(names) + " County"
attrs = pd.concat([attrs[~attrs.fips.str.startswith("09")], ct], ignore_index=True)
attrs.to_parquet("data/county_attrs.parquet", index=False)
print(f"county attribute rows: {len(attrs):,}; with RUCC: {attrs.rucc_2023.notna().sum():,}")
