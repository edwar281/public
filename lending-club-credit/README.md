# Did the Rate Cover the Risk?

Credit losses, realized investor returns and a profit-based approval policy on the public
**Lending Club** loan data: 2.26M funded loans and 27.6M rejected applications, 2007–2018.

**Author:** Will Edwards, PhD. Assisted by Claude.

**[View the live dashboard →](https://edwar281.github.io/public/lending-club-credit/dashboard/)**  
(Or open `dashboard/index.html` locally; it is self-contained.)

## Headline results

| | |
|---|---|
| Matured book (36-month loans, 2010–2015, 613K loans, $7.7B) | 5.6% annual realized return to investors; 13.9% of loans charged off |
| Return by sub-grade | Peaks at 6.7% (B4); falls to 2.1% in grade G, which paid 25% interest and saw 40% default |
| Vintage deterioration | 12 months after issue: 3.2% of 2016 dollars charged off vs. 2.0% for 2014 |
| Out-of-time test (2015 loans, 283K) | AUC 0.704 (model with grade) · 0.693 (borrower data only) · 0.678 (LC sub-grade alone) |
| Riskiest model decile | Paid a 16.4% average rate; 34% defaulted; lifetime return −1.1% |
| Within grade D | Rate spread under 0.5 pt across model-risk fifths; returns from +8.9% (safest) to −1.7% (riskiest) |
| Approval policy | Declining the riskiest 5% by model adds $5.7M (+3%) to 2015 investor profit; sub-grade alone adds at most $0.6M |

## Pipeline

```
make data        # scripts/download.sh: Kaggle API download (~1.4 GB)
make parquet     # src/00_to_parquet.py: both CSVs -> Parquet via DuckDB
make dashboard   # 01_loans -> 02_model -> 03_build_dashboard
```

| Step | What it does |
|---|---|
| `00_to_parquet.py` | Converts the accepted (151 columns) and rejected files to Parquet. |
| `01_loans.py` | Cleans outcomes, places charge-off 5 months after the last payment (LC's 150-day rule), builds vintage loss curves with an observation-window guard, rebuilds monthly cash flows for every matured loan, and solves pool IRRs by grade and sub-grade. Also the application funnel. |
| `02_model.py` | LightGBM on application-time fields only, trained on Jan 2013–Sep 2014, early-stopped on Q4 2014, scored once on 2015. Three nested rankings, decile returns, within-grade risk, profit curve for declining the riskiest k%, SHAP. |
| `03_build_dashboard.py` | Injects results into `dashboard/template.html` and writes the self-contained `dashboard/index.html`. |

Runs on 4 CPU cores and 16 GB RAM in about five minutes after download.

## Design notes

- **Out-of-time validation.** Train on 2013–14 issues, test on 2015, the way a credit model would be used.
- **Leakage controls.** No payment history, last FICO pull, hardship or settlement fields. Bureau fields LC began reporting in late 2015 are excluded so train and test see the same inputs.
- **Investor's view of profit.** Payments net of the 1% servicing fee, plus recoveries net of collection fees, minus dollars lent. Undiscounted, before funding costs.
- **Selection.** The model only sees approved loans, so it cannot say how rejected applicants would have performed.

## Data

Lending Club loan data 2007–2018Q4, Kaggle dataset `wordsforthewise/lending-club`. The data is not redistributed here.
