# Full pipeline. Download ~1.4 GB; runs on 4 cores / 16 GB RAM.
all: dashboard
data: ; bash scripts/download.sh
parquet: ; python src/00_to_parquet.py
loans: ; python src/01_loans.py
model: loans ; python src/02_model.py
dashboard: loans model ; python src/03_build_dashboard.py
.PHONY: all data parquet loans model dashboard
