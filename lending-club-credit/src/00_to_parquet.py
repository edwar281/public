"""Convert the two Lending Club CSVs (2.26M accepted loans, 27.6M rejected applications) to Parquet.

The accepted file has 151 columns, many sparsely populated; DuckDB's sniffer reads the whole file
to type them. The last rows of the accepted file are summary footers ("Total amount funded in
policy code ..."), which have no id that parses as a loan; we drop rows with a null loan_amnt.
"""
import duckdb

con = duckdb.connect()
con.sql("SET preserve_insertion_order=false")
con.sql("""
COPY (
  SELECT * FROM read_csv('data/raw/accepted_2007_to_2018Q4.csv.gz', header=true, sample_size=-1,
                         all_varchar=false, ignore_errors=false)
  WHERE loan_amnt IS NOT NULL
) TO 'data/accepted.parquet' (FORMAT parquet, COMPRESSION zstd)""")
con.sql("""
COPY (
  SELECT "Amount Requested" AS amount, "Application Date"::DATE AS app_date, "Loan Title" AS title,
         "Risk_Score" AS risk_score,
         TRY_CAST(replace("Debt-To-Income Ratio", '%', '') AS DOUBLE) AS dti,
         "Zip Code" AS zip3, "State" AS state, "Employment Length" AS emp_length, "Policy Code" AS policy_code
  FROM read_csv('data/raw/rejected_2007_to_2018Q4.csv.gz', header=true, sample_size=-1)
) TO 'data/rejected.parquet' (FORMAT parquet, COMPRESSION zstd)""")
for t in ("accepted", "rejected"):
    print(t, con.sql(f"select count(*) from 'data/{t}.parquet'").fetchone()[0])
