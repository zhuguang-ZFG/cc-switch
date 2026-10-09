#!/usr/bin/env python3
"""Add ch186 tierflow qwen3.8-flash pool to NewAPI.

tierflow.cn tierflow_pro/tierflow both route to qwen3.8-flash underneath.
This channel aggregates them as backup capacity for the qwen3.8-flash model.

Key: sk-06Hlo7uyrHSQ0FyG67nATB6WXBQWUERTD9syrfu4FovQRXnT
Base URL: https://tierflow.cn/v1
Models: tierflow_pro, tierflow (both map to qwen3.8-flash)
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".new-api-local" / "new-api.db"

CHANNEL_ID = 186
CHANNEL_NAME = "tierflow-qwen38-flash"
BASE_URL = "https://tierflow.cn"  # NewAPI appends /v1 automatically
KEY = "sk-06Hlo7uyrHSQ0FyG67nATB6WXBQWUERTD9syrfu4FovQRXnT"

# Expose as qwen3.8-flash for OMP aggregation; model_mapping routes to upstream tierflow_pro
MODELS = "qwen3.8-flash"
MODEL_MAPPING = json.dumps({
    "qwen3.8-flash": "tierflow_pro",
})

PRIORITY = -30  # Low priority, backup only
WEIGHT = 1
STATUS = 1  # Enabled


def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # Check if channel already exists
    cur.execute("SELECT id FROM channels WHERE id = ?", (CHANNEL_ID,))
    if cur.fetchone():
        print(f"ch{CHANNEL_ID} already exists, skipping")
        conn.close()
        return

    # Insert new channel
    import time
    created_time = int(time.time())
    cur.execute("""
        INSERT INTO channels (
            id, type, key, name, base_url, models, model_mapping,
            priority, weight, status, created_time, auto_ban
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        CHANNEL_ID,
        1,  # type=1 for OpenAI-compatible
        KEY,
        CHANNEL_NAME,
        BASE_URL,
        MODELS,
        MODEL_MAPPING,
        PRIORITY,
        WEIGHT,
        STATUS,
        created_time,
        1,  # auto_ban enabled
    ))

    conn.commit()
    print(f"Created ch{CHANNEL_ID}: {CHANNEL_NAME}")
    print(f"  Models: {MODELS}")
    print(f"  Mapping: {MODEL_MAPPING}")
    print(f"  Priority: {PRIORITY}, Weight: {WEIGHT}, Status: {STATUS}")

    conn.close()


if __name__ == "__main__":
    main()
