#!/usr/bin/env python3
"""Ensure restored agentrouter channels carry BLOB-typed channel_info matching live rows.

Background: NewAPI distributor threw
  sql: Scan error on column index 28, name "channel_info": unexpected end of JSON input
after restored channels (from backup INSERT) got TEXT/NULL channel_info while live
rows (ch118/ch180) store BLOB. This writes valid compact JSON as BLOB.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".new-api-local" / "new-api.db"

SINGLE = (
    '{"is_multi_key":false,"multi_key_size":0,'
    '"multi_key_status_list":null,"multi_key_polling_index":0,"multi_key_mode":""}'
)
POLL5 = (
    '{"is_multi_key":true,"multi_key_size":5,'
    '"multi_key_status_list":{},"multi_key_polling_index":0,"multi_key_mode":"polling"}'
)


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    conn.execute("UPDATE channels SET channel_info = CAST(? AS BLOB) WHERE id = 127", (POLL5,))
    for ch_id in [57, 72, 86, 134, 135, 136]:
        conn.execute(
            "UPDATE channels SET channel_info = CAST(? AS BLOB) WHERE id = ?",
            (SINGLE, ch_id),
        )
    conn.commit()

    rows = conn.execute(
        "SELECT id, typeof(channel_info) AS t, channel_info FROM channels "
        "WHERE id IN (57,72,86,118,127,134,135,136,180) ORDER BY id"
    ).fetchall()
    for r in rows:
        ci = r["channel_info"]
        if isinstance(ci, bytes):
            ci = ci.decode("utf-8", "replace")
        print(f"ch{r['id']}: typeof={r['t']} info={(ci or 'None')[:80]}")
    conn.close()


if __name__ == "__main__":
    main()
