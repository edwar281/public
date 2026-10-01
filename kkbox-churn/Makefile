# Full pipeline. Raw download ~9 GB; peak disk ~45 GB; runs on 2 cores / 8 GB RAM.
all: dashboard
data: ; bash scripts/download.sh
parquet: ; python src/00a_to_parquet.py && 7z x -so data/raw/user_logs.csv.7z | python src/00b_logs_to_parquet.py
spells: ; python src/01_spells.py
survival: spells ; python src/02_survival.py
features: spells ; python src/03_features.py
model: features ; python src/04_model.py
dashboard: survival model ; python src/05_build_dashboard.py
.PHONY: all data parquet spells survival features model dashboard
