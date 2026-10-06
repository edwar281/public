# Closing the Broadband Gap

Seven years of US internet speed tests, county by county, from **Ookla Open Data**:
62M fixed and mobile tile-quarters covering 594M speed tests, Q1 2019 to Q2 2026.

**Author:** Will Edwards, PhD. Assisted by Claude.

**[View the live dashboard →](https://edwar281.github.io/public/ookla-broadband/dashboard/)**  
(Or open `dashboard/index.html` locally; it is self-contained.)

## Headline results

| | |
|---|---|
| National median home download | 112 → 370 Mbps (2019 vs. Q3 2025–Q2 2026); mobile 31 → 248 Mbps |
| Rural counties (RUCC 8–9) | 21 → 204 Mbps; share of tests meeting FCC 100/20 rose from 6% to 64% |
| Rural vs. large-metro speed | 17% → 53% of large-metro speed, but the absolute gap widened from 102 to 179 Mbps |
| Rural gap, controls added | −43% raw → −35% with income and state → −11% once population density is added |
| Convergence | Slope −0.76: counties that started 10% slower grew ~8% faster, conditional on income, rurality and state |
| Disruption detector | 17 of 58,950 county-quarters flagged, including Buncombe and Henderson counties, NC, after Hurricane Helene (Q4 2024) |

## Pipeline

```
make data        # src/00_ingest.py: download 60 global quarterly files (~20 GB) from the public S3 bucket, keep US tiles
make counties    # src/01_counties.py: tile centroid -> county (point-in-polygon), county attributes
make panel       # src/02_panel.py: county x quarter x network panel, test-weighted medians
make dashboard   # 03_analysis -> 04_anomalies -> 05_build_dashboard
```

| Step | What it does |
|---|---|
| `00_ingest.py` | Streams each global quarter (~6M zoom-16 tiles), decodes quadkeys to tile centroids, keeps tiles inside US bounding boxes, and deletes the raw file. |
| `01_counties.py` | Assigns 6.0M unique tiles to counties with a shapely STRtree. Joins USDA 2023 Rural-Urban Continuum Codes, 2020 population, land area and ACS income, handling FIPS changes (Oglala Lakota, Valdez-Cordova, Connecticut planning regions). |
| `02_panel.py` | DuckDB over ~62M tile-quarters: test-weighted medians of download, upload and latency, and the share of tests meeting 100/20, by county, by rurality group and nationally. |
| `03_analysis.py` | Rural gap over time, county regressions (rurality, income, density, state FE, state-clustered SEs), convergence model, county map (Albers projection, simplified SVG paths). |
| `04_anomalies.py` | Robust z-scores of test volume, speed and latency against each county's own quadratic trend after removing national seasonality; flags test surges that coincide with slower speeds or higher latency. |
| `05_build_dashboard.py` | Injects results into `dashboard/template.html` and writes the self-contained `dashboard/index.html`. |

Runs on 4 CPU cores and 16 GB RAM. Peak disk use is about 2 GB, since raw files are deleted as they are filtered.

## Design notes

- **Weighting.** Ookla publishes per-tile averages, not individual tests, so every statistic is weighted by test count, and medians are used because multi-gigabit tiles skew means.
- **Self-selection.** People test more when something feels wrong, so measured speeds describe tests, not households. The disruption detector turns that bias into the signal.
- **Pooled windows.** 2019 and the latest four quarters are pooled to remove seasonality.
- **Associations, not causal effects.** The regressions describe where speeds differ, not why any one county got faster.

## Data

Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps (CC BY-NC-SA 4.0), from `s3://ookla-open-data`. County boundaries: Census (via plotly/datasets). County attributes: USDA ERS Rural-Urban Continuum Codes 2023, 2020 Census and 2018–22 ACS, as compiled in the [`rurality`](https://github.com/cwimpy/rurality) R package (MIT). The two small reference files are in `ref/`. Raw Ookla data is not redistributed here; `make data` downloads it.
