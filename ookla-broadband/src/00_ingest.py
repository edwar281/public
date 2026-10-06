"""Download Ookla Open Data quarterly tiles (fixed + mobile) and keep only US tiles.

Each global quarter file is ~350 MB / ~6M zoom-16 tiles. We stream one file at a time,
decode the quadkey to the tile centroid, keep tiles inside rough US bounding boxes
(county assignment in 01_counties.py removes the Canada/Mexico spill-over), and delete the raw file.

Output: data/us/{type}_{YYYY}Q{q}.parquet with quadkey, lon, lat and the performance columns.
"""
import os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pyarrow as pa, pyarrow.parquet as pq

BUCKET = "https://ookla-open-data.s3.amazonaws.com/parquet/performance"
RAW, OUT = "data/raw", "data/us"
COLS = ["quadkey", "avg_d_kbps", "avg_u_kbps", "avg_lat_ms", "tests", "devices"]
BOXES = [(-125.0, -66.5, 24.3, 49.6),   # contiguous US
         (-180.0, -129.0, 51.0, 71.6),  # Alaska
         (-160.6, -154.6, 18.7, 22.4)]  # Hawaii


def quadkey_centroid(qk):
    """Vectorised quadkey -> (lon, lat) of the tile centre."""
    a = np.frombuffer("".join(qk).encode(), dtype=np.uint8).reshape(len(qk), -1) - 48
    z = a.shape[1]
    w = (1 << np.arange(z - 1, -1, -1)).astype(np.int64)
    x = ((a & 1).astype(np.int64) * w).sum(1)
    y = ((a >> 1).astype(np.int64) * w).sum(1)
    n = float(1 << z)
    lon = (x + 0.5) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y + 0.5) / n))))
    return lon, lat


def one(kind, year, q):
    out = f"{OUT}/{kind}_{year}Q{q}.parquet"
    if os.path.exists(out):
        return out
    month = f"{3 * (q - 1) + 1:02d}"
    name = f"{year}-{month}-01_performance_{kind}_tiles.parquet"
    url = f"{BUCKET}/type={kind}/year={year}/quarter={q}/{name}"
    raw = f"{RAW}/{name}"
    subprocess.run(["curl", "-fsS", "--retry", "4", "-o", raw, url], check=True)
    t = pq.read_table(raw, columns=COLS)
    lon, lat = quadkey_centroid(t["quadkey"].to_pylist())
    keep = np.zeros(len(lon), bool)
    for x0, x1, y0, y1 in BOXES:
        keep |= (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
    t = t.append_column("lon", pa.array(lon)).append_column("lat", pa.array(lat)).filter(pa.array(keep))
    pq.write_table(t, out + ".tmp", compression="zstd")
    os.replace(out + ".tmp", out)
    os.remove(raw)
    print(f"{kind} {year}Q{q}: {len(keep):,} global tiles -> {t.num_rows:,} US-box tiles", flush=True)
    return out


if __name__ == "__main__":
    os.makedirs(RAW, exist_ok=True); os.makedirs(OUT, exist_ok=True)
    last = (2026, 2)
    jobs = [(k, y, q) for k in ("fixed", "mobile") for y in range(2019, last[0] + 1)
            for q in range(1, 5) if (y, q) <= last]
    with ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 3) as ex:
        list(ex.map(lambda j: one(*j), jobs))
