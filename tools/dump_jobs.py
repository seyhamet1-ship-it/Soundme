import sqlite3
import json

conn = sqlite3.connect('jobs.sqlite3')
rows = conn.execute('SELECT id, updated_at, payload FROM jobs ORDER BY updated_at DESC').fetchall()
print(f'Total jobs: {len(rows)}')
for jid, up, payload in rows:
    p = json.loads(payload)
    err = p.get('error')
    err_code = err.get('code') if isinstance(err, dict) else err
    err_msg = err.get('message_en', '') if isinstance(err, dict) else ''
    dur = p.get('duration') or p.get('video_duration') or 0
    num_segs = len(p.get('segments') or [])
    print(f"{jid} | status={p.get('status')} | stage={p.get('stage')} | prog={p.get('progress')} | dur={dur} | segs={num_segs} | err_code={err_code} | err_msg={err_msg[:80]}")
