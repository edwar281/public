"""
03_features.py — Point-in-time (leakage-safe) features for the two labelled cohorts.

  Feb-2017 cohort  (train.csv,    ~993K members)  cutoff 2017-01-31  -> model development
  Mar-2017 cohort  (train_v2.csv, ~971K members)  cutoff 2017-02-28  -> out-of-time test

Every feature uses only data dated on/before the cutoff, i.e. before the expiration month
in which the churn label is decided. Usage features come from ~400M daily listening rows.
"""
import duckdb, os

DATA = os.environ.get("KKBOX_PQ", "data/pq")
DB = os.environ.get("KKBOX_DB", "data/kkbox.duckdb")
OUT = os.environ.get("KKBOX_FEAT", "data/features")
os.makedirs(OUT, exist_ok=True)

con = duckdb.connect(DB)
con.execute("SET memory_limit='5GB'; SET threads=2; SET enable_progress_bar=false; SET preserve_insertion_order=false")

COHORTS = {"feb": ("train", "2017-01-31"), "mar": ("train_v2", "2017-02-28")}

for name, (label_file, cutoff) in COHORTS.items():
    C = f"DATE '{cutoff}'"
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE pop AS
    SELECT i.uid, l.is_churn FROM '{DATA}/{label_file}.parquet' l JOIN '{DATA}/idmap.parquet' i USING (msno)""")

    # ---------- transactions (history up to cutoff) ----------
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE f_txn AS
    WITH t AS (SELECT t.* FROM txn t JOIN pop USING (uid) WHERE tdate <= {C}),
    o AS (SELECT *, row_number() OVER (PARTITION BY uid ORDER BY tdate DESC, cancel DESC, edate ASC) rk FROM t),
    last AS (SELECT * FROM o WHERE rk = 1),
    lastsub AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY uid ORDER BY tdate DESC, edate DESC) r
                               FROM t WHERE cancel = 0) WHERE r = 1),
    agg AS (
      SELECT uid, count(*) n_txn, sum(cancel) n_cancel, count(DISTINCT pm) n_pay_methods,
             count(DISTINCT plan_days) n_plan_lengths,
             date_diff('day', min(tdate), {C}) days_since_first_txn,
             sum(paid) total_paid,
             sum(CASE WHEN tdate > {C} - INTERVAL 90 DAY THEN 1 ELSE 0 END) n_txn_90d,
             sum(CASE WHEN tdate > {C} - INTERVAL 90 DAY THEN cancel ELSE 0 END) n_cancel_90d,
             avg(auto_renew) share_auto_renew,
             avg(CASE WHEN paid < list_price THEN 1 ELSE 0 END) share_discounted,
             date_diff('day', max(CASE WHEN cancel = 1 THEN tdate END), {C}) days_since_cancel
      FROM t GROUP BY uid),
    -- prior lapses re-derived from history <= cutoff only (the global `cycles` table looks past the cutoff)
    seqd AS (SELECT *, row_number() OVER (PARTITION BY uid ORDER BY tdate, cancel, edate) seq FROM t),
    bnd AS (SELECT *, lead(cancel) OVER (PARTITION BY uid ORDER BY seq) nc,
                   min(CASE WHEN cancel = 0 THEN tdate END) OVER (PARTITION BY uid ORDER BY seq
                       ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) nsub FROM seqd),
    lapses AS (SELECT uid, sum(CASE WHEN nsub IS NOT NULL THEN (date_diff('day', edate, nsub) >= 30)::INT
                                    WHEN edate + INTERVAL 30 DAY <= {C} THEN 1 ELSE 0 END) n_prior_lapses
               FROM bnd WHERE nc = 0 OR nc IS NULL GROUP BY uid)
    SELECT a.*, coalesce(lp.n_prior_lapses, 0) n_prior_lapses,
           l.cancel last_is_cancel, l.auto_renew last_auto_renew, l.pm last_pm,
           date_diff('day', {C}, l.edate) days_to_expiry,
           s.plan_days last_plan_days, s.list_price last_list_price, s.paid last_paid,
           s.list_price - s.paid last_discount, s.paid / greatest(s.plan_days, 1) last_price_per_day,
           date_diff('day', s.tdate, {C}) days_since_last_sub
    FROM agg a JOIN last l USING (uid) LEFT JOIN lastsub s USING (uid) LEFT JOIN lapses lp USING (uid)""")

    # ---------- daily usage (400M rows; read only the 180 days before cutoff) ----------
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE f_use AS
    WITH u AS (
      SELECT uid, date, num_25, num_50, num_75, num_985, num_100, num_unq,
             least(greatest(total_secs, 0), 86400) secs,
             date_diff('day', date, {C}) ago
      FROM '{DATA}/user_logs.parquet'          -- v1 logs run through 2017-02-28, covering both cutoffs
      WHERE date <= {C} AND date > {C} - INTERVAL 180 DAY AND uid IN (SELECT uid FROM pop))
    SELECT uid,
      {", ".join(
        f"count(*) FILTER (WHERE ago < {w}) days_active_{w}d, "
        f"coalesce(sum(secs) FILTER (WHERE ago < {w}),0)/3600.0 hours_{w}d, "
        f"coalesce(sum(num_unq) FILTER (WHERE ago < {w}),0) uniq_songs_{w}d"
        for w in (7, 14, 30, 90, 180))},
      sum(num_25) FILTER (WHERE ago < 30) / nullif(sum(num_25+num_50+num_75+num_985+num_100) FILTER (WHERE ago < 30),0) skip_share_30d,
      sum(num_100) FILTER (WHERE ago < 30) / nullif(sum(num_25+num_50+num_75+num_985+num_100) FILTER (WHERE ago < 30),0) complete_share_30d,
      min(ago) days_since_last_active
    FROM u GROUP BY uid""")

    con.execute(f"""
    COPY (
      SELECT p.uid, p.is_churn,
             m.city, CASE WHEN m.bd BETWEEN 13 AND 80 THEN m.bd END age,
             CASE m.gender WHEN 'male' THEN 1 WHEN 'female' THEN 0 END is_male,
             m.registered_via,
             date_diff('day', strptime(m.registration_init_time::VARCHAR,'%Y%m%d')::DATE, {C}) days_since_registration,
             t.* EXCLUDE (uid), u.* EXCLUDE (uid),
             u.hours_7d / nullif(u.hours_30d / 30.0 * 7, 0) usage_trend_7v30,
             u.hours_30d / nullif(u.hours_90d / 3.0, 0) usage_trend_30v90
      FROM pop p
      LEFT JOIN (SELECT i.uid, m.* FROM '{DATA}/members_v3.parquet' m JOIN '{DATA}/idmap.parquet' i USING (msno)) m USING (uid)
      LEFT JOIN f_txn t USING (uid)
      LEFT JOIN f_use u USING (uid)
    ) TO '{OUT}/{name}.parquet' (FORMAT parquet)""")
    print(name, con.execute(f"SELECT count(*), avg(is_churn), count(hours_30d) FROM '{OUT}/{name}.parquet'").fetchone())
