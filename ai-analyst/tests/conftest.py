import os
import pathlib
import sys

import duckdb
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "runner")]


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    """Tiny stand-in for the Ookla parquet files, with the real column names."""
    d = tmp_path_factory.mktemp("data")
    con = duckdb.connect()
    con.execute("""CREATE TABLE counties AS SELECT * FROM (VALUES
        ('08031', 'CO', 'Denver County', 715522, 153.3, 4667.0, 1, 'Large metro', 85641.0),
        ('08053', 'CO', 'Hinsdale County', 788, 1117.0, 0.7, 9, 'Rural', 61000.0))
        t(fips, state, county_name, population_2020, land_area_sqmi, people_per_sqmi, rucc_2023,
          area_group, median_household_income)""")
    con.execute("""CREATE TABLE county_quarterly AS SELECT * FROM (VALUES
        ('08031', '2019Q1', 'fixed', 900, 50000, 20000, 120.5, 15.2, 14, 140.0, 0.55, 0.05),
        ('08031', '2026Q2', 'fixed', 950, 61000, 24000, 410.0, 40.1, 11, 450.0, 0.90, 0.01),
        ('08053', '2019Q1', 'fixed', 12, 80, 40, 9.5, 1.1, 45, 12.0, 0.00, 0.80),
        ('08053', '2026Q2', 'fixed', 15, 95, 50, 95.0, 12.0, 30, 110.0, 0.40, 0.20))
        t(fips, quarter, network, tiles, tests, devices, median_download_mbps, median_upload_mbps,
          median_latency_ms, mean_download_mbps, share_tests_100_20, share_tests_below_25_3)""")
    con.execute("""CREATE TABLE area_quarterly AS SELECT * FROM (VALUES
        ('National', 'fixed', '2019Q1', 1400000, 16900000, 100.4, 15.6, 16, 0.30),
        ('National', 'fixed', '2026Q2', 1700000, 21000000, 370.0, 45.0, 12, 0.78))
        t(area_group, network, quarter, tiles, tests, median_download_mbps, median_upload_mbps,
          median_latency_ms, share_tests_100_20)""")
    con.execute("""CREATE TABLE disruptions AS SELECT * FROM (VALUES
        ('37021', '2024Q4', 'NC', 'Buncombe County', 30000.0, 60.0, 30.0, 6.1, -5.2, 4.0, 'Hurricane Helene'))
        t(fips, quarter, state, county_name, tests, median_download_mbps, median_latency_ms,
          z_tests, z_download, z_latency, known_event)""")
    for t in ("counties", "county_quarterly", "area_quarterly", "disruptions"):
        con.execute(f"COPY {t} TO '{d}/{t}.parquet' (FORMAT parquet)")
    os.environ["DATA_DIR"] = str(d)
    return d
