import base64
import json
import platform
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from src.results import DeliveryAttempt, DictationRecord, HistoryStore
from src.utils import AppError, ScreamerError


def record(identifier="one", **changes):
    return DictationRecord(
        id=identifier,
        created_at="2026-09-30T12:00:00+00:00",
        raw_text="Sensitive raw transcript",
        **changes,
    )


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory, "history.enc")
        self.store = HistoryStore(self.path)
        # A reversible fake exercises the storage boundary, not DPAPI security.
        self.enterContext(patch("src.config.protect_bytes", side_effect=base64.b64encode))
        self.enterContext(patch("src.config.unprotect_bytes", side_effect=base64.b64decode))

    def test_raw_and_final_with_attempt_roundtrip_without_audio_or_credentials(self):
        attempt = DeliveryAttempt(
            "2026-09-30T12:01:00+00:00",
            "copy_and_type",
            copied=True,
            text_state="reported_failed",
            text_events_submitted=3,
            reason="injection_failed",
        )
        entry = record(
            rewritten_text="Sensitive cleaned transcript",
            rewrite_status="succeeded",
            warnings=(AppError.STT_FALLBACK_USED,),
            last_delivery=attempt,
        )
        self.assertEqual(self.store.upsert(entry, 100), [entry])
        self.assertEqual(self.store.load(), [entry])
        self.assertNotIn(b"Sensitive", self.path.read_bytes())
        payload = json.loads(base64.b64decode(self.path.read_bytes()))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(set(payload["entries"][0]), set(asdict(entry)))
        self.assertNotIn("audio", payload["entries"][0])
        self.assertNotIn("config", payload["entries"][0])

    def test_upsert_checkpoint_updates_one_id_and_retention_evicts_oldest(self):
        self.store.upsert(record(), 2)
        final = record(rewrite_status="succeeded", rewritten_text="Final")
        self.assertEqual(self.store.upsert(final, 2), [final])
        second = replace(record("two"), created_at="2026-09-30T12:02:00+00:00")
        third = replace(record("three"), created_at="2026-09-30T12:03:00+00:00")
        self.store.upsert(second, 2)
        self.assertEqual(self.store.upsert(third, 2), [second, third])
        self.assertEqual(self.store.upsert(third, 1), [third])

    def test_one_hundred_and_first_entry_never_grows_store_beyond_one_hundred(self):
        for index in range(101):
            self.store.upsert(record(f"entry-{index:03d}"), 100)
        entries = self.store.load()
        self.assertEqual(len(entries), 100)
        self.assertEqual(entries[0].id, "entry-001")
        self.assertEqual(entries[-1].id, "entry-100")

    def test_corrupt_file_cannot_be_overwritten_by_normal_commit(self):
        self.path.write_bytes(b"undecryptable original")
        with self.assertRaises(ScreamerError) as failure:
            self.store.upsert(record(), 100)
        self.assertEqual(failure.exception.code, AppError.HISTORY_STORAGE_FAILED)
        self.assertEqual(self.path.read_bytes(), b"undecryptable original")
        self.store.clear()
        self.assertEqual(self.store.load(), [])

    def test_unknown_version_bad_record_and_duplicate_ids_fail_closed(self):
        data = asdict(record())
        data["warnings"] = []
        for payload in (
            {"version": 2, "entries": []},
            {"version": True, "entries": []},
            {"version": 1, "entries": [dict(data, raw_text="")]},
            {"version": 1, "entries": [dict(data, warnings=["UNKNOWN"])]},
            {"version": 1, "entries": [data, data]},
            {"version": 1, "entries": [dict(data, audio="must never be accepted")]},
        ):
            with self.subTest(payload=payload):
                original = base64.b64encode(json.dumps(payload).encode())
                self.path.write_bytes(original)
                with self.assertRaises(ScreamerError):
                    self.store.upsert(record("new"), 100)
                self.assertEqual(self.path.read_bytes(), original)

    def test_failed_atomic_replace_preserves_prior_committed_entries_and_removes_temp(self):
        self.store.upsert(record(), 100)
        original = self.path.read_bytes()
        with patch("src.results.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(ScreamerError):
                self.store.upsert(record("new"), 100)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_delete_and_clear_failures_leave_committed_record_visible(self):
        self.store.upsert(record(), 100)
        with patch("src.results.os.replace", side_effect=OSError("read-only")):
            with self.assertRaises(ScreamerError):
                self.store.delete("one")
        self.assertEqual(self.store.load(), [record()])
        with patch.object(Path, "unlink", side_effect=OSError("read-only")):
            with self.assertRaises(ScreamerError):
                self.store.clear()
        self.assertEqual(self.store.load(), [record()])
        self.assertEqual(self.store.delete("one"), [])
        self.store.clear()
        self.assertFalse(self.path.exists())

    def test_failed_or_cancelled_rewrite_final_is_raw(self):
        for status in ("not_requested", "pending", "failed", "cancelled"):
            with self.subTest(status=status):
                self.assertEqual(
                    record(rewrite_status=status).final_text, "Sensitive raw transcript"
                )

    def test_valid_oversized_file_is_read_only_until_bounded_explicit_commit(self):
        entries = []
        for index in range(101):
            entry = asdict(record(f"entry-{index:03d}"))
            entry["warnings"] = []
            entries.append(entry)
        original = base64.b64encode(json.dumps({"version": 1, "entries": entries}).encode())
        self.path.write_bytes(original)
        self.assertEqual(len(self.store.load()), 100)
        self.assertEqual(len(self.store.trim(1)), 1)
        self.assertEqual(self.path.read_bytes(), original)
        remaining = self.store.delete("entry-100", 1)
        self.assertEqual([entry.id for entry in remaining], ["entry-099"])
        self.assertEqual(self.store.load(), remaining)


class WindowsHistoryTests(unittest.TestCase):
    @unittest.skipUnless(platform.system() == "Windows", "DPAPI requires Windows")
    def test_real_dpapi_history_roundtrip_keeps_transcript_out_of_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "history.enc")
            store = HistoryStore(path)
            store.upsert(record(), 100)
            self.assertNotIn(b"Sensitive raw transcript", path.read_bytes())
            self.assertEqual(store.load(), [record()])


if __name__ == "__main__":
    unittest.main()
