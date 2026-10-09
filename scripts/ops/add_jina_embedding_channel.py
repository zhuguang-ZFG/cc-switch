#!/usr/bin/env python3
"""Add ch187 Jina AI embedding models to NewAPI.

Jina AI provides high-quality embedding models as backup for muyuan-gongyi (ch173).

Key: jina_cc30f9e51d22462b888f36db3196d6db-ASKWFSodA8p2ChSmOKmggQINTNP
Base URL: https://api.jina.ai
Models: text-embedding-3-small, text-embedding-3-large
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import requests

DB_PATH = Path.home() / ".new-api-local" / "new-api.db"

CHANNEL_ID = 187
CHANNEL_NAME = "jina-embedding"
BASE_URL = "https://api.jina.ai"
KEY = "jina_cc30f9e51d22462b888f36db3196d6db-ASKWFSodA8p2ChSmOKmggQINTNP"

MODELS = "jina-embeddings-v3"
MODEL_MAPPING = ""

PRIORITY = -20
WEIGHT = 1
STATUS = 1


def add_channel():
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    
    cur.execute("SELECT id FROM channels WHERE id = ?", (CHANNEL_ID,))
    if cur.fetchone():
        print(f"ch{CHANNEL_ID} already exists, skipping")
        conn.close()
        return
    
    created_time = int(time.time())
    cur.execute("""
        INSERT INTO channels (
            id, type, key, name, base_url, models, model_mapping,
            priority, weight, status, created_time, auto_ban
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        CHANNEL_ID,
        1,
        KEY,
        CHANNEL_NAME,
        BASE_URL,
        MODELS,
        MODEL_MAPPING,
        PRIORITY,
        WEIGHT,
        STATUS,
        created_time,
        1,
    ))
    
    conn.commit()
    print(f"Created ch{CHANNEL_ID}: {CHANNEL_NAME}")
    print(f"  Models: {MODELS}")
    print(f"  Priority: {PRIORITY}, Weight: {WEIGHT}")
    conn.close()


def test_embeddings():
    print("\n[TEST] Testing Jina AI embeddings...")
    
    headers = {
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json"
    }
    
    for model in MODELS.split(","):
        model = model.strip()
        payload = {
            "model": model,
            "input": ["Test embedding text"]
        }
        
        try:
            start = time.time()
            resp = requests.post(
                f"{BASE_URL}/v1/embeddings",
                headers=headers,
                json=payload,
                timeout=10
            )
            latency = int((time.time() - start) * 1000)
            
            if resp.status_code == 200:
                data = resp.json()
                dim = len(data["data"][0]["embedding"])
                print(f"[OK] {model}: {latency}ms, {dim}d")
            else:
                print(f"[FAIL] {model}: HTTP {resp.status_code} - {resp.text[:100]}")
        except Exception as e:
            print(f"[FAIL] {model}: {e}")


if __name__ == "__main__":
    add_channel()
    test_embeddings()
    print("\n[DONE] Jina AI embedding channel added")
