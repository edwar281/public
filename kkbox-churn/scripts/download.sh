#!/usr/bin/env bash
# Download the WSDM-KKBox churn data from Kaggle (~9 GB compressed).
# Prereqs: accept the competition rules at https://www.kaggle.com/c/kkbox-churn-prediction-challenge/rules
#          and put your Kaggle API token in ~/.kaggle/access_token (or export KAGGLE_API_TOKEN).
set -euo pipefail
TOKEN="${KAGGLE_API_TOKEN:-$(cat ~/.kaggle/access_token)}"
mkdir -p data/raw && cd data/raw
for f in members_v3.csv.7z train.csv.7z train_v2.csv.7z transactions.csv.7z transactions_v2.csv.7z user_logs.csv.7z; do
  [ -s "$f" ] && { echo "have $f"; continue; }
  curl -fSL -C - -H "Authorization: Bearer $TOKEN" -o "$f" \
    "https://www.kaggle.com/api/v1/competitions/data/download/kkbox-churn-prediction-challenge/$f"
done
