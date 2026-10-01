"""
00a_to_parquet.py — Convert the smaller KKBox tables to Parquet and build the integer member-id map.
Usage: python src/00a_to_parquet.py      (expects data/raw/*.csv.7z; needs the `7z` binary)
"""
import duckdb, subprocess, os
os.makedirs("data/pq", exist_ok=True)
con = duckdb.connect(); con.execute("SET memory_limit='4GB'; SET enable_progress_bar=false")
for n in ["members_v3", "train", "train_v2", "transactions", "transactions_v2"]:
    subprocess.run(f"7z x -so data/raw/{n}.csv.7z > data/raw/{n}.csv", shell=True, check=True)
    con.execute(f"COPY (SELECT * FROM read_csv('data/raw/{n}.csv', header=true, auto_detect=true)) "
                f"TO 'data/pq/{n}.parquet' (FORMAT parquet, COMPRESSION zstd)")
    os.remove(f"data/raw/{n}.csv")
    print(n, con.execute(f"SELECT count(*) FROM 'data/pq/{n}.parquet'").fetchone()[0])
con.execute("""COPY (SELECT msno, row_number() OVER (ORDER BY msno)::INTEGER AS uid FROM (
  SELECT msno FROM 'data/pq/members_v3.parquet' UNION SELECT msno FROM 'data/pq/train.parquet'
  UNION SELECT msno FROM 'data/pq/train_v2.parquet' UNION SELECT msno FROM 'data/pq/transactions.parquet'
  UNION SELECT msno FROM 'data/pq/transactions_v2.parquet')) TO 'data/pq/idmap.parquet' (FORMAT parquet)""")
print("idmap", con.execute("SELECT count(*) FROM 'data/pq/idmap.parquet'").fetchone()[0])
