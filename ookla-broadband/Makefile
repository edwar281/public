# Full pipeline. Downloads ~20 GB from the public Ookla S3 bucket, keeping ~1 GB of US tiles; runs on 4 cores / 16 GB RAM.
all: dashboard
data: ; python src/00_ingest.py 3
counties: ; python src/01_counties.py
panel: counties ; python src/02_panel.py
analysis: panel ; python src/03_analysis.py
anomalies: panel ; python src/04_anomalies.py
dashboard: analysis anomalies ; python src/05_build_dashboard.py
.PHONY: all data counties panel analysis anomalies dashboard
