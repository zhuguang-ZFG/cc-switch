"""ch3 复探哨兵：回捞门禁必须挡住垮塌期的空 200 假阳性。"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "ch3_100xlabs_reprobe", Path(__file__).with_name("ch3-100xlabs-reprobe.py")
)
assert SPEC and SPEC.loader
reprobe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reprobe)


def event(kind: str, **fields) -> bytes:
    payload = json.dumps({"type": kind, **fields})
    return f"event: {kind}\ndata: {payload}\n\n".encode()


def completed_stream(text: str = "pong") -> bytes:
    return (
        event("message_start", message={"role": "assistant", "content": []})
        + event("content_block_start", index=0, content_block={"type": "text", "text": ""})
        + event("content_block_delta", index=0, delta={"type": "text_delta", "text": text})
        + event("content_block_stop", index=0)
        + event("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 2})
        + event("message_stop")
    )


def empty_stream() -> bytes:
    """事故口径：上游 200 结束但无内容无计费。"""
    return (
        event("message_start", message={"role": "assistant", "content": []})
        + event("message_delta", delta={"stop_reason": "end_turn"})
        + event("message_stop")
    )


class ClassifyProbeTests(unittest.TestCase):
    def test_full_stream_is_ok(self):
        ok, detail = reprobe.classify_probe(200, completed_stream())
        self.assertTrue(ok, detail)

    def test_empty_200_is_not_ok(self):
        """垮塌期假阳性主力：200 + 无 content 块 → 必须判 fail。"""
        ok, detail = reprobe.classify_probe(200, empty_stream())
        self.assertFalse(ok)
        self.assertIn("content", detail)

    def test_200_without_usage_is_not_ok(self):
        body = (
            event("message_start", message={"role": "assistant", "content": []})
            + event("content_block_start", index=0, content_block={"type": "text", "text": ""})
            + event("content_block_delta", index=0, delta={"type": "text_delta", "text": "pong"})
            + event("content_block_stop", index=0)
            + event("message_stop")
        )
        ok, detail = reprobe.classify_probe(200, body)
        self.assertFalse(ok)
        self.assertIn("usage", detail)

    def test_upstream_error_status_is_not_ok(self):
        for status in (429, 500, 502, 503):
            ok, _ = reprobe.classify_probe(status, b"")
            self.assertFalse(ok, status)

    def test_sse_error_event_is_not_ok(self):
        ok, detail = reprobe.classify_probe(200, event("error", error={"type": "overloaded_error"}))
        self.assertFalse(ok)
        self.assertIn("error", detail.lower())

    def test_oversized_body_is_not_ok(self):
        ok, detail = reprobe.classify_probe(200, b"x" * (reprobe.MAX_RESPONSE_BYTES + 1))
        self.assertFalse(ok)
        self.assertIn("size limit", detail)


class ReenableGateTests(unittest.TestCase):
    def test_all_ok_reenables(self):
        self.assertTrue(reprobe.should_reenable([True, True, True, True]))

    def test_single_failure_blocks(self):
        """3/4 通过仍不回捞：部分成功=池未稳，继续观察。"""
        self.assertFalse(reprobe.should_reenable([True, True, True, False]))

    def test_empty_verdicts_never_reenables(self):
        self.assertFalse(reprobe.should_reenable([]))


class DisabledStatusTests(unittest.TestCase):
    def test_manual_and_auto_disabled_are_in_scope(self):
        self.assertTrue(reprobe.is_disabled(2))
        self.assertTrue(reprobe.is_disabled(3))

    def test_enabled_is_steady_state(self):
        self.assertFalse(reprobe.is_disabled(1))


class PickKeysTests(unittest.TestCase):
    def test_rotation_covers_all_keys(self):
        keys = ["a", "b", "c", "d", "e", "f"]
        seen = set()
        cursor = 0
        for _ in range(3):
            picked = reprobe.pick_keys(keys, cursor, 2)
            self.assertEqual(2, len(picked))
            seen.update(picked)
            cursor = (cursor + 2) % len(keys)
        self.assertEqual(set(keys), seen)

    def test_fewer_keys_than_requested(self):
        self.assertEqual(["only"], reprobe.pick_keys(["only"], 0, 2))

    def test_empty_keys(self):
        self.assertEqual([], reprobe.pick_keys([], 0, 2))


class LockTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.lock = Path(directory) / "test.lock"
        self.enterContext(patch.object(reprobe, "LOCK_FILE", self.lock))

    def test_fresh_lock_blocks_second_instance(self):
        self.assertTrue(reprobe.acquire_lock())
        try:
            self.assertFalse(reprobe.acquire_lock())
        finally:
            reprobe.release_lock()
        self.assertFalse(self.lock.exists())

    def test_stale_lock_is_taken_over(self):
        self.lock.write_text("dead-pid", encoding="utf-8")
        stale = time.time() - reprobe.LOCK_STALE_SECONDS - 60
        os.utime(self.lock, (stale, stale))
        self.assertTrue(reprobe.acquire_lock())
        reprobe.release_lock()



class AbilitiesEnabledTests(unittest.TestCase):
    """回捞完整性：status=1 但 abilities.enabled=0 的半开态必须检出。"""

    def setUp(self):
        import sqlite3

        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.db = Path(directory) / "test.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE abilities (channel_id INTEGER, model TEXT, enabled INTEGER)")
        conn.executemany(
            "INSERT INTO abilities VALUES (3, ?, ?)",
            [("claude-opus-5-5", 1), ("claude-fable-5-1", 1), ("claude-fable-5.1", 1)],
        )
        conn.commit()
        conn.close()
        self.enterContext(patch.object(reprobe, "NEWAPI_DB", self.db))

    def _set_enabled(self, model: str, enabled: int):
        import sqlite3

        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE abilities SET enabled=? WHERE model=?", (enabled, model))
        conn.commit()
        conn.close()

    def test_all_enabled_passes(self):
        ok, detail = reprobe.abilities_enabled()
        self.assertTrue(ok, detail)
        self.assertIn("3 rows", detail)

    def test_single_disabled_row_is_half_open(self):
        self._set_enabled("claude-fable-5.1", 0)
        ok, detail = reprobe.abilities_enabled()
        self.assertFalse(ok)
        self.assertIn("claude-fable-5.1", detail)

    def test_missing_rows_fail_closed(self):
        import sqlite3

        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM abilities")
        conn.commit()
        conn.close()
        ok, detail = reprobe.abilities_enabled()
        self.assertFalse(ok)
        self.assertIn("无 abilities", detail)


if __name__ == "__main__":
    unittest.main()
