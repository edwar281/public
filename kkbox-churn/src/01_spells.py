"""
01_spells.py — Build renewal-cycle spells and churn (lapse) events from KKBox transactions.

Churn definition mirrors the official WSDM labeller: after a membership's effective expiration,
the subscriber churns if no new (non-cancel) subscription transaction occurs within 30 days.
Cancellations pull the effective expiration forward.

Outputs (DuckDB tables in data/kkbox.duckdb):
  txn        cleaned, de-duplicated transactions (v1 + v2) with integer user ids
  cycles     one row per renewal boundary: effective expiry, gap to next subscription, lapse flag
  new_subs   acquisition cohort of genuinely new paying subscribers, with time-to-first-churn and
             baseline covariates (for Kaplan-Meier and Cox models)
"""
import duckdb, os

DATA = os.environ.get("KKBOX_PQ", "data/pq")
DB = os.environ.get("KKBOX_DB", "data/kkbox.duckdb")
OBS_END = "2017-02-28"      # last date with complete transaction history for all members (v1 files)

con = duckdb.connect(DB)
con.execute("SET memory_limit='3GB'; SET threads=2; SET enable_progress_bar=false")

con.execute(f"""
CREATE OR REPLACE TABLE txn AS
SELECT i.uid, payment_method_id pm, payment_plan_days plan_days, plan_list_price list_price,
       actual_amount_paid paid, is_auto_renew auto_renew, is_cancel cancel,
       strptime(transaction_date::VARCHAR,'%Y%m%d')::DATE tdate,
       strptime(membership_expire_date::VARCHAR,'%Y%m%d')::DATE edate
FROM (SELECT * FROM '{DATA}/transactions.parquet'
      UNION SELECT * FROM '{DATA}/transactions_v2.parquet') t
JOIN '{DATA}/idmap.parquet' i USING (msno)
WHERE membership_expire_date >= 20150101            -- drop corrupt 1970 expirations
""")

# Order within member/day as the labeller does: subscription precedes cancellation.
# A "boundary" is the last transaction before the next subscription (or the member's last transaction):
# its expiry is the effective expiry at the moment the next subscription happens.
con.execute(f"""
CREATE OR REPLACE TABLE cycles AS
WITH o AS (
  SELECT *, row_number() OVER (PARTITION BY uid ORDER BY tdate, cancel, edate) seq
  FROM txn WHERE tdate <= DATE '{OBS_END}'
), n AS (
  SELECT *,
    lead(cancel) OVER (PARTITION BY uid ORDER BY seq) AS next_cancel,
    min(CASE WHEN cancel=0 THEN tdate END) OVER (PARTITION BY uid ORDER BY seq
        ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) AS next_sub_date
  FROM o
)
SELECT uid, seq, tdate, edate AS eff_exp, next_sub_date,
       date_diff('day', edate, next_sub_date) AS gap,
       CASE WHEN next_sub_date IS NOT NULL THEN (date_diff('day', edate, next_sub_date) >= 30)::INT
            WHEN edate + INTERVAL 30 DAY <= DATE '{OBS_END}' THEN 1
            ELSE NULL END AS lapse     -- NULL = still active / not yet observable (censored)
FROM n
WHERE next_cancel = 0 OR next_cancel IS NULL
""")

# Acquisition cohort: registered 2015+, first PAID subscription (free trials tracked as a covariate) Mar-2015..Dec-2016
# (>= 2 months of look-back so we are not mistaking a returning member for a new one).
con.execute(f"""
CREATE OR REPLACE TABLE new_subs AS
WITH first_txn AS (
  SELECT * FROM (
    SELECT t.*, row_number() OVER (PARTITION BY uid ORDER BY tdate, cancel, edate) r
    FROM txn t WHERE cancel = 0 AND paid > 0 AND tdate <= DATE '{OBS_END}') WHERE r = 1
), trial AS (            -- free (0-paid) subscriptions before the first paid one = trial origin
  SELECT t.uid, count(*) n_trial FROM txn t JOIN first_txn f USING (uid)
  WHERE t.cancel = 0 AND t.paid = 0 AND t.tdate < f.tdate GROUP BY t.uid
), first_lapse AS (
  SELECT c.uid, min(c.eff_exp) AS lapse_date FROM cycles c JOIN first_txn f USING (uid)
  WHERE c.lapse = 1 AND c.eff_exp >= f.tdate GROUP BY c.uid
), mem AS (
  SELECT i.uid, city, bd, gender, registered_via,
         strptime(registration_init_time::VARCHAR,'%Y%m%d')::DATE reg_date
  FROM '{DATA}/members_v3.parquet' m JOIN '{DATA}/idmap.parquet' i USING (msno)
)
SELECT f.uid, f.tdate AS start_date, date_trunc('month', f.tdate) AS cohort_month,
       f.plan_days, f.list_price, f.paid, f.auto_renew, f.pm,
       (tr.n_trial IS NOT NULL)::INT AS from_trial,
       (f.paid < f.list_price)::INT AS discounted_start,
       m.city, CASE WHEN m.bd BETWEEN 13 AND 80 THEN m.bd END AS age, m.gender, m.registered_via,
       l.lapse_date IS NOT NULL AS event,
       date_diff('day', f.tdate,
                 coalesce(l.lapse_date, DATE '{OBS_END}' - INTERVAL 30 DAY)) AS duration
FROM first_txn f
JOIN mem m USING (uid)
LEFT JOIN first_lapse l USING (uid)
LEFT JOIN trial tr USING (uid)
WHERE f.tdate BETWEEN DATE '2015-03-01' AND DATE '2016-12-31'
  AND m.reg_date >= DATE '2015-01-01'
""")

for q in ["SELECT count(*) n, count(DISTINCT uid) users, min(tdate), max(tdate) FROM txn",
          "SELECT count(*) boundaries, avg(lapse) lapse_rate, count(lapse) observed FROM cycles",
          "SELECT count(*) n, avg(event::INT) churned, median(duration) med_dur, min(duration), max(duration) FROM new_subs"]:
    print(con.execute(q).fetchdf().to_string(index=False))
