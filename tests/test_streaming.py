import unittest

from src.audio import SAMPLE_RATE, AudioSnapshot
from src.streaming import OnlineTranscriptionSession
from src.stt import TranscriptionResult, TranscriptSegment


def _snapshot() -> AudioSnapshot:
    return AudioSnapshot(b"wav", 0, 64000, 4.0)


class OnlineTranscriptionSessionTests(unittest.TestCase):
    def test_local_agreement_commits_stable_whole_word_prefix(self) -> None:
        session = OnlineTranscriptionSession(hold_back_words=2)
        session.accept(_snapshot(), TranscriptionResult("I want to ship this feature"))
        update = session.accept(_snapshot(), TranscriptionResult("I want to ship this feature today"))

        self.assertEqual(update.committed_delta, "I want to ship")
        self.assertEqual(update.transcript, "I want to ship")

    def test_already_committed_text_is_not_duplicated(self) -> None:
        session = OnlineTranscriptionSession(hold_back_words=1)
        session.accept(_snapshot(), TranscriptionResult("Let's test the batching"))
        session.accept(_snapshot(), TranscriptionResult("Let's test the batching implementation"))
        update = session.accept(_snapshot(), TranscriptionResult("Let's test the batching implementation again"))

        self.assertEqual(update.transcript, "Let's test the batching")

    def test_finalization_commits_remaining_tail(self) -> None:
        session = OnlineTranscriptionSession(hold_back_words=2)
        session.accept(_snapshot(), TranscriptionResult("Let's test the"))
        session.accept(_snapshot(), TranscriptionResult("Let's test the batching implementation"))

        final = session.accept(_snapshot(), TranscriptionResult("Let's test the batching implementation"), final=True)

        self.assertEqual(final.transcript, "Let's test the batching implementation")

    def test_punctuation_only_hypothesis_is_ignored(self) -> None:
        session = OnlineTranscriptionSession()
        update = session.accept(_snapshot(), TranscriptionResult("..."))

        self.assertEqual(update.transcript, "")

    def test_dot_batch_followed_by_valid_final_commits_valid_text(self) -> None:
        session = OnlineTranscriptionSession()
        session.accept(_snapshot(), TranscriptionResult("..."))
        final = session.accept(_snapshot(), TranscriptionResult("The deployment finished successfully"), final=True)

        self.assertEqual(final.transcript, "The deployment finished successfully")

    def test_regressing_candidate_does_not_duplicate_committed_prefix(self) -> None:
        session = OnlineTranscriptionSession(hold_back_words=0)
        session.accept(_snapshot(), TranscriptionResult("A B C D E"))
        session.accept(_snapshot(), TranscriptionResult("A B C D E F"))
        update = session.accept(_snapshot(), TranscriptionResult("A B C D"))

        self.assertEqual(update.committed_delta, "")
        self.assertEqual(update.transcript, "A B C D E")

    def test_segments_skip_overlap_and_live_edge_until_stable_delay(self) -> None:
        session = OnlineTranscriptionSession(overlap_s=1.0, stable_delay_s=1.0)
        snapshot = AudioSnapshot(b"wav", 10 * SAMPLE_RATE, 18 * SAMPLE_RATE, 8.0)
        result = TranscriptionResult(
            "overlap words stable words live edge",
            segments=[
                TranscriptSegment("overlap words", start=0.1, end=1.5),
                TranscriptSegment("stable words", start=2.0, end=4.0),
                TranscriptSegment("live edge", start=7.2, end=8.0),
            ],
        )

        session.accept(snapshot, result)
        update = session.accept(snapshot, result)

        self.assertEqual(update.committed_delta, "stable words")
        self.assertEqual(update.transcript, "stable words")
        self.assertEqual(update.committed_audio_sample, 14 * SAMPLE_RATE)

    def test_window_and_final_start_follow_committed_audio(self) -> None:
        session = OnlineTranscriptionSession(overlap_s=1.0, max_window_s=24.0, stable_delay_s=0.0)
        snapshot = AudioSnapshot(b"wav", 0, 10 * SAMPLE_RATE, 10.0)
        result = TranscriptionResult(
            "committed text",
            segments=[TranscriptSegment("committed text", start=2.0, end=4.0)],
        )

        session.accept(snapshot, result)
        session.accept(snapshot, result)

        self.assertEqual(session.window_start_sample(40 * SAMPLE_RATE), 16 * SAMPLE_RATE)
        self.assertEqual(session.final_window_start_sample(), 4 * SAMPLE_RATE)


if __name__ == "__main__":
    unittest.main()
