import base64
import json
import platform
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from src.config import OUTPUT_MODES
from src.results import DELIVERY_MODES, DeliveryAttempt, DictationRecord, HistoryStore
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

    def test_paste_delivery_attempt_persists_and_reloads_from_a_new_store(self):
        attempt = DeliveryAttempt(
            "2026-09-30T12:01:00+00:00",
            "paste",
            text_state="events_submitted",
            text_events_submitted=4,
        )
        entry = record(last_delivery=attempt)
        self.assertEqual(self.store.upsert(entry, 100), [entry])
        self.assertEqual(HistoryStore(self.path).load(), [entry])

    def test_every_configurable_output_mode_and_manual_insert_can_be_stored(self):
        configurable = {key for key, _label in OUTPUT_MODES}
        self.assertEqual(DELIVERY_MODES, configurable | {"manual"})

    def test_records_from_before_a_field_was_added_still_load(self):
        attempt = DeliveryAttempt("2026-09-30T12:01:00+00:00", "copy")
        stored = asdict(record(last_delivery=attempt))
        del stored["profile_id"], stored["language"], stored["warnings"]
        del stored["last_delivery"]["reason"]
        self.path.write_bytes(
            base64.b64encode(json.dumps({"version": 1, "entries": [stored]}).encode())
        )
        self.assertEqual(self.store.load(), [record(last_delivery=attempt)])

    def test_record_missing_required_field_fails_closed(self):
        stored = asdict(record())
        stored["warnings"] = []
        del stored["raw_text"]
        self.path.write_bytes(
            base64.b64encode(json.dumps({"version": 1, "entries": [stored]}).encode())
        )
        with self.assertRaises(ScreamerError):
            self.store.load()

    def test_upsert_checkpoint_updates_one_id_and_retention_evicts_oldest(self):
        self.store.upsert(record(), 2)
        final = record(rewrite_status="succeeded", rewritten_text="Final")
        self.assertEqual(self.store.upsert(final, 2), [final])
        second = replace(record("two"), created_at="2026-09-30T12:02:00+00:00")
        third = replace(record("three"), created_at="2026-09-30T12:03:00+00:00")
        self.store.upsert(second, 2)
        self.assertEqual(self.store.upsert(third, 2), [second, third])
        self.assertEqual(self.store.upsert(third, 1), [third])

    def upsert_numbered_entries(self, count, limit):
        for index in range(count):
            self.store.upsert(record(f"entry-{index:03d}"), limit)

    def test_one_hundred_and_first_entry_never_grows_store_beyond_one_hundred(self):
        self.upsert_numbered_entries(101, 100)
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

    def stored_record(self):
        data = asdict(record())
        data["warnings"] = []
        return data

    def assert_payload_fails_closed(self, payload):
        original = base64.b64encode(json.dumps(payload).encode())
        self.path.write_bytes(original)
        with self.assertRaises(ScreamerError):
            self.store.upsert(record("new"), 100)
        self.assertEqual(self.path.read_bytes(), original)

    def test_unknown_version_fails_closed(self):
        self.assert_payload_fails_closed({"version": 2, "entries": []})

    def test_boolean_version_fails_closed(self):
        self.assert_payload_fails_closed({"version": True, "entries": []})

    def test_record_with_empty_raw_text_fails_closed(self):
        data = self.stored_record()
        self.assert_payload_fails_closed({"version": 1, "entries": [dict(data, raw_text="")]})

    def test_record_with_unknown_warning_fails_closed(self):
        data = self.stored_record()
        self.assert_payload_fails_closed(
            {"version": 1, "entries": [dict(data, warnings=["UNKNOWN"])]}
        )

    def test_duplicate_ids_fail_closed(self):
        data = self.stored_record()
        self.assert_payload_fails_closed({"version": 1, "entries": [data, data]})

    def test_record_with_audio_field_fails_closed(self):
        data = self.stored_record()
        self.assert_payload_fails_closed(
            {"version": 1, "entries": [dict(data, audio="must never be accepted")]}
        )

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
        statuses = ("not_requested", "pending", "failed", "cancelled")
        self.assertEqual(
            {status: record(rewrite_status=status).final_text for status in statuses},
            dict.fromkeys(statuses, "Sensitive raw transcript"),
        )

    def test_valid_oversized_file_is_read_only_until_bounded_explicit_commit(self):
        entries = [dict(asdict(record(f"entry-{index:03d}")), warnings=[]) for index in range(101)]
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
