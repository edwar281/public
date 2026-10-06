#!/usr/bin/env bash
# Download the Lending Club data from Kaggle (~1.4 GB zip) and keep the two gzipped CSVs.
# Prereq: a Kaggle API token in ~/.kaggle/access_token (or export KAGGLE_API_TOKEN).
# Alternatively download the zip in a browser from https://www.kaggle.com/datasets/wordsforthewise/lending-club
# and place it at data/raw/archive.zip.
set -euo pipefail
TOKEN="${KAGGLE_API_TOKEN:-$(cat ~/.kaggle/access_token 2>/dev/null || true)}"
mkdir -p data/raw && cd data/raw
if [ ! -s archive.zip ]; then
  curl -fSL -C - -H "Authorization: Bearer $TOKEN" -o archive.zip \
    "https://www.kaggle.com/api/v1/datasets/download/wordsforthewise/lending-club"
fi
unzip -o -j archive.zip accepted_2007_to_2018Q4.csv.gz rejected_2007_to_2018Q4.csv.gz
