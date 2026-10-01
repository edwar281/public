"""
00b_logs_to_parquet.py — Stream the 30 GB user_logs.csv (392M rows) into compressed Parquet on a small machine.

Usage:  7z x -so data/raw/user_logs.csv.7z | python src/00b_logs_to_parquet.py
Maps the 44-char base64 member id to a 4-byte integer key (data/pq/idmap.parquet), narrows numeric types and
writes zstd Parquet in 4M-row groups: 30 GB CSV -> ~4.4 GB, peak memory ~4 GB.
"""
import sys, time, pyarrow as pa, pyarrow.csv as pc, pyarrow.parquet as pq, pandas as pd, numpy as np
t=time.time()
idm=pq.read_table('data/pq/idmap.parquet').to_pandas()
index=pd.Index(idm.msno.values); uids=idm.uid.values.astype(np.int32); del idm
schema=pa.schema([('uid',pa.int32()),('date',pa.date32()),('num_25',pa.int16()),('num_50',pa.int16()),('num_75',pa.int16()),('num_985',pa.int16()),('num_100',pa.int32()),('num_unq',pa.int32()),('total_secs',pa.float32())])
ct={'msno':pa.string(),'date':pa.string(),'num_25':pa.int32(),'num_50':pa.int32(),'num_75':pa.int32(),'num_985':pa.int32(),'num_100':pa.int32(),'num_unq':pa.int32(),'total_secs':pa.float64()}
rd=pc.open_csv(sys.stdin.buffer, read_options=pc.ReadOptions(block_size=64<<20), convert_options=pc.ConvertOptions(column_types=ct))
w=pq.ParquetWriter('data/pq/user_logs.parquet', schema, compression='zstd')
n=miss=0
for b in rd:
    pos=index.get_indexer(b.column('msno').to_numpy(zero_copy_only=False))
    ok=pos>=0; miss+=int((~ok).sum())
    d=pd.to_datetime(pd.Series(b.column('date').to_numpy(zero_copy_only=False)), format='%Y%m%d').values.astype('datetime64[D]')
    c=lambda name,typ: pa.array(b.column(name).to_numpy()[ok].astype(typ))
    tb=pa.table({'uid':pa.array(uids[pos[ok]]),'date':pa.array(d[ok]),'num_25':c('num_25',np.int16),'num_50':c('num_50',np.int16),
       'num_75':c('num_75',np.int16),'num_985':c('num_985',np.int16),'num_100':c('num_100',np.int32),'num_unq':c('num_unq',np.int32),
       'total_secs':c('total_secs',np.float32)}, schema=schema)
    w.write_table(tb, row_group_size=4_000_000); n+=b.num_rows
    if n % 20_000_000 < b.num_rows: print(n, miss, round(time.time()-t), flush=True)
w.close(); print('DONE rows',n,'unmapped',miss,'secs',round(time.time()-t), flush=True)
