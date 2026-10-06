"""Build the county x quarter x network-type panel from ~50M US tile-quarters.

Ookla publishes per-tile averages, not individual tests, so every statistic here is
test-weighted over tiles: a tile with 40 tests counts 40 times. "Median download" is the
test-weighted median of tile-average speeds. "100/20 share" is the share of tests taken in
tiles whose average met the FCC's 100 Mbps down / 20 Mbps up broadband benchmark.
"""
import duckdb

con = duckdb.connect()
con.sql("SET preserve_insertion_order=false")
con.sql("""
CREATE TABLE t AS
SELECT regexp_extract(x.filename, '(fixed|mobile)_', 1) kind,
       regexp_extract(x.filename, '_(\\d{4}Q\\d)', 1) q,
       c.fips, avg_d_kbps / 1000.0 d, avg_u_kbps / 1000.0 u, avg_lat_ms lat, tests, devices
FROM read_parquet('data/us/*.parquet', filename=true) x
JOIN 'data/quadkey_county.parquet' c USING (quadkey)""")
print(con.sql("SELECT kind, count(*) tiles, sum(tests) tests FROM t GROUP BY 1"))

wmed = lambda col, grp: f"""
  SELECT {grp}, min({col}) FILTER (WHERE cw >= 0.5 * tot) med_{col} FROM (
    SELECT {grp}, {col}, sum(tests) OVER (PARTITION BY {grp} ORDER BY {col}
                                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) cw,
           sum(tests) OVER (PARTITION BY {grp}) tot
    FROM t) GROUP BY {grp}"""

G = "kind, q, fips"
con.sql(f"""
CREATE TABLE panel AS
SELECT a.*, b.med_d, c.med_u, e.med_lat FROM (
  SELECT {G}, count(*) tiles, sum(tests) tests, sum(devices) devices,
         sum(d * tests) / sum(tests) mean_d, sum(u * tests) / sum(tests) mean_u,
         sum(lat * tests) / sum(tests) mean_lat,
         sum(tests) FILTER (WHERE d >= 100 AND u >= 20) / sum(tests) share_100_20,
         sum(tests) FILTER (WHERE d < 25 OR u < 3) / sum(tests) share_below_25_3
  FROM t GROUP BY ALL) a
JOIN ({wmed('d', G)}) b USING (kind, q, fips)
JOIN ({wmed('u', G)}) c USING (kind, q, fips)
JOIN ({wmed('lat', G)}) e USING (kind, q, fips)""")
con.sql("COPY panel TO 'data/panel.parquet' (FORMAT parquet)")

# National series, plus by rurality group (computed from tiles, not by averaging counties)
con.sql("""CREATE TABLE ta AS SELECT t.*, CASE
      WHEN a.rucc_2023 = 1 THEN '1 Large metro'
      WHEN a.rucc_2023 IN (2, 3) THEN '2 Smaller metro'
      WHEN a.rucc_2023 IN (4, 5, 6, 7) THEN '3 Nonmetro town'
      WHEN a.rucc_2023 IN (8, 9) THEN '4 Rural' END grp
    FROM t LEFT JOIN 'data/county_attrs.parquet' a USING (fips)""")
con.sql("DROP TABLE t"); con.sql("ALTER TABLE ta RENAME TO t")
for name, G in (("national", "kind, q"), ("by_group", "kind, q, grp")):
    con.sql(f"""COPY (
      SELECT a.*, b.med_d, c.med_u, e.med_lat FROM (
        SELECT {G}, count(*) tiles, sum(tests) tests, sum(devices) devices,
               sum(tests) FILTER (WHERE d >= 100 AND u >= 20) / sum(tests) share_100_20,
               sum(d * tests) / sum(tests) mean_d
        FROM t GROUP BY ALL) a
      JOIN ({wmed('d', G)}) b USING ({G}) JOIN ({wmed('u', G)}) c USING ({G})
      JOIN ({wmed('lat', G)}) e USING ({G}) ORDER BY ALL
    ) TO 'data/{name}.parquet' (FORMAT parquet)""")
print(con.sql("SELECT * FROM 'data/national.parquet' WHERE q IN ('2019Q1','2022Q2','2026Q2')"))
print(con.sql("SELECT count(*), count(DISTINCT fips) FROM panel"))
