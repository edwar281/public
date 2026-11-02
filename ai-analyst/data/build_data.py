"""Build the analyst's DuckDB database from the Ookla broadband pipeline outputs.

Run the ookla-broadband pipeline through `make panel` first, then:
    python data/build_data.py <ookla-broadband dir> data/parquet

Writes one zstd parquet file per table. The runner container loads them into DuckDB.
"""
import json
import pathlib
import sys

import duckdb

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
d = src / "data"

con = duckdb.connect()
con.sql(f"""
CREATE TABLE counties AS
SELECT fips, state_abbr AS state, county_name,
       CAST(pop_2020 AS INTEGER) AS population_2020, land_area_sqmi,
       pop_density AS people_per_sqmi, CAST(rucc_2023 AS INTEGER) AS rucc_2023,
       CASE WHEN rucc_2023 = 1 THEN 'Large metro'
            WHEN rucc_2023 IN (2, 3) THEN 'Smaller metro'
            WHEN rucc_2023 BETWEEN 4 AND 7 THEN 'Nonmetro town'
            WHEN rucc_2023 IN (8, 9) THEN 'Rural' END AS area_group,
       median_income AS median_household_income
FROM '{d}/county_attrs.parquet' ORDER BY fips""")

con.sql(f"""
CREATE TABLE county_quarterly AS
SELECT fips, q AS quarter, kind AS network, tiles, CAST(tests AS BIGINT) AS tests,
       CAST(devices AS BIGINT) AS devices,
       round(med_d, 2) AS median_download_mbps, round(med_u, 2) AS median_upload_mbps,
       med_lat AS median_latency_ms, round(mean_d, 2) AS mean_download_mbps,
       round(share_100_20, 4) AS share_tests_100_20,
       round(share_below_25_3, 4) AS share_tests_below_25_3
FROM '{d}/panel.parquet' ORDER BY fips, network, quarter""")

con.sql(f"""
CREATE TABLE area_quarterly AS
SELECT 'National' AS area_group, kind AS network, q AS quarter, tiles, CAST(tests AS BIGINT) AS tests,
       round(med_d, 2) AS median_download_mbps, round(med_u, 2) AS median_upload_mbps,
       med_lat AS median_latency_ms, round(share_100_20, 4) AS share_tests_100_20
FROM '{d}/national.parquet'
UNION ALL
SELECT substr(grp, 3), kind, q, tiles, CAST(tests AS BIGINT), round(med_d, 2), round(med_u, 2),
       med_lat, round(share_100_20, 4)
FROM '{d}/by_group.parquet' WHERE grp IS NOT NULL
ORDER BY 1, 2, 3""")

anom = json.loads((src / "out/anomalies.json").read_text())
con.execute("""CREATE TABLE disruptions (fips VARCHAR, quarter VARCHAR, state VARCHAR, county_name VARCHAR,
               tests DOUBLE, median_download_mbps DOUBLE, median_latency_ms DOUBLE,
               z_tests DOUBLE, z_download DOUBLE, z_latency DOUBLE, known_event VARCHAR)""")
con.executemany("INSERT INTO disruptions VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
    (f["fips"], f["q"], f["state_abbr"], f["county_name"], f["tests"], f["med_d"], f["med_lat"],
     f["z_tests"], f["z_med_d"], f["z_med_lat"], f["event"] or None) for f in anom["flagged"]])

for t in ("counties", "county_quarterly", "area_quarterly", "disruptions"):
    n, = con.execute(f"SELECT count(*) FROM {t}").fetchone()
    con.execute(f"COPY {t} TO '{out}/{t}.parquet' (FORMAT parquet, COMPRESSION zstd)")
    print(f"{t}: {n:,} rows, {(out / f'{t}.parquet').stat().st_size / 1e6:.1f} MB")
con.close()
