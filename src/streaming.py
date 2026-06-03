"""Online transcription assembly for rolling STT snapshots."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.audio import SAMPLE_RATE, AudioSnapshot
from src.stt import TranscriptionResult, is_meaningful_transcript
from src.utils import AppError

log = logging.getLogger(__name__)


_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class StreamingUpdate:
    committed_delta: str = ""
    transcript: str = ""
    committed_audio_sample: int = 0
    warnings: list[AppError] = field(default_factory=list)


@dataclass(frozen=True)
class _WordTiming:
    end_sample: int


class OnlineTranscriptionSession:
    def __init__(
        self,
        batch_interval_s: float = 4.0,
        overlap_s: float = 1.0,
        max_window_s: float = 24.0,
        hold_back_words: int = 2,
        stable_delay_s: float = 0.8,
    ) -> None:
        self.batch_interval_s = batch_interval_s
        self.overlap_s = overlap_s
        self.max_window_s = max_window_s
        self.hold_back_words = hold_back_words
        self.stable_delay_s = stable_delay_s
        self._previous_hypothesis = ""
        self._committed_words: list[str] = []
        self._committed_audio_sample = 0
        self._warnings: list[AppError] = []

    @property
    def committed_text(self) -> str:
        return " ".join(self._committed_words).strip()

    @property
    def warnings(self) -> list[AppError]:
        return list(self._warnings)

    @property
    def committed_audio_sample(self) -> int:
        return self._committed_audio_sample

    def window_start_sample(self, current_sample: int, sample_rate: int = SAMPLE_RATE) -> int:
        overlap_samples = int(self.overlap_s * sample_rate)
        max_window_samples = int(self.max_window_s * sample_rate)
        context_start = max(0, self._committed_audio_sample - overlap_samples)
        max_window_start = max(0, current_sample - max_window_samples)
        return max(context_start, max_window_start)

    def final_window_start_sample(self) -> int:
        return max(0, self._committed_audio_sample)

    def trim_before_sample(self, sample_rate: int = SAMPLE_RATE) -> int:
        overlap_samples = int(self.overlap_s * sample_rate)
        return max(0, self._committed_audio_sample - overlap_samples)

    def accept(self, snapshot: AudioSnapshot, result: TranscriptionResult, final: bool = False) -> StreamingUpdate:
        self._warnings.extend(result.warnings)

        if not is_meaningful_transcript(result.text):
            log.debug(
                "Streaming batch: speech=false final=%s window=%.2f-%.2f duration=%.2f candidate_chars=%d committed_delta_chars=0 candidate_debug=%r",
                final,
                snapshot.start_sample / SAMPLE_RATE,
                snapshot.end_sample / SAMPLE_RATE,
                snapshot.duration,
                len(result.text),
                result.text[:80],
            )
            return StreamingUpdate(
                transcript=self.committed_text,
                committed_audio_sample=self._committed_audio_sample,
                warnings=list(result.warnings),
            )

        candidate_text, timings, has_segments = self._candidate(snapshot, result, final)
        if not candidate_text:
            log.debug(
                "Streaming batch: speech=%s final=%s window=%.2f-%.2f duration=%.2f candidate_chars=0 committed_delta_chars=0 candidate_debug=''",
                is_meaningful_transcript(result.text),
                final,
                snapshot.start_sample / SAMPLE_RATE,
                snapshot.end_sample / SAMPLE_RATE,
                snapshot.duration,
            )
            self._previous_hypothesis = ""
            return StreamingUpdate(
                transcript=self.committed_text,
                committed_audio_sample=self._committed_audio_sample,
                warnings=list(result.warnings),
            )

        if final:
            candidate_words = _words(candidate_text)
        elif not self._previous_hypothesis:
            candidate_words = []
        else:
            stable_words = _longest_common_word_prefix(
                _words(self._previous_hypothesis),
                _words(candidate_text),
            )
            candidate_words = _hold_back(stable_words, self.hold_back_words if not has_segments else 0)

        delta_words = self._new_words(candidate_words)
        self._previous_hypothesis = candidate_text
        if not delta_words:
            log.debug(
                "Streaming batch: speech=true final=%s window=%.2f-%.2f duration=%.2f candidate_chars=%d committed_delta_chars=0 candidate_debug=%r",
                final,
                snapshot.start_sample / SAMPLE_RATE,
                snapshot.end_sample / SAMPLE_RATE,
                snapshot.duration,
                len(candidate_text),
                candidate_text[:80],
            )
            return StreamingUpdate(
                transcript=self.committed_text,
                committed_audio_sample=self._committed_audio_sample,
                warnings=list(result.warnings),
            )

        delta = " ".join(delta_words).strip()
        if not is_meaningful_transcript(delta):
            return StreamingUpdate(
                transcript=self.committed_text,
                committed_audio_sample=self._committed_audio_sample,
                warnings=list(result.warnings),
            )

        self._committed_words.extend(delta_words)
        if timings:
            committed_candidate_count = min(len(candidate_words), len(timings))
            if committed_candidate_count:
                self._committed_audio_sample = max(
                    self._committed_audio_sample,
                    timings[committed_candidate_count - 1].end_sample,
                )
        elif final:
            self._committed_audio_sample = max(self._committed_audio_sample, snapshot.end_sample)
        log.debug(
            "Streaming commit: speech=true final=%s window=%.2f-%.2f duration=%.2f candidate_chars=%d committed_delta_chars=%d delta_words=%d transcript_words=%d trim_before=%.2f candidate_debug=%r",
            final,
            snapshot.start_sample / SAMPLE_RATE,
            snapshot.end_sample / SAMPLE_RATE,
            snapshot.duration,
            len(candidate_text),
            len(delta),
            len(delta_words),
            len(self._committed_words),
            self.trim_before_sample() / SAMPLE_RATE,
            candidate_text[:160],
        )
        return StreamingUpdate(
            committed_delta=delta,
            transcript=self.committed_text,
            committed_audio_sample=self._committed_audio_sample,
            warnings=list(result.warnings),
        )

    def finalize_hypothesis(self) -> str:
        if not is_meaningful_transcript(self._previous_hypothesis):
            return self.committed_text
        tail_words = self._new_words(_words(self._previous_hypothesis))
        if tail_words:
            self._committed_words.extend(tail_words)
        return self.committed_text

    def _candidate(
        self,
        snapshot: AudioSnapshot,
        result: TranscriptionResult,
        final: bool,
    ) -> tuple[str, list[_WordTiming], bool]:
        if not result.segments:
            return result.text, [], False

        safe_texts: list[str] = []
        timings: list[_WordTiming] = []
        live_edge_sample = snapshot.end_sample if final else snapshot.end_sample - int(self.stable_delay_s * SAMPLE_RATE)
        overlap_edge_sample = snapshot.start_sample if final else snapshot.start_sample + int(self.overlap_s * SAMPLE_RATE)

        for segment in result.segments:
            text = segment.text.strip()
            if not text:
                continue
            start = snapshot.start_sample if segment.start is None else snapshot.start_sample + int(segment.start * SAMPLE_RATE)
            end = snapshot.end_sample if segment.end is None else snapshot.start_sample + int(segment.end * SAMPLE_RATE)
            in_overlap_region = snapshot.start_sample > 0 and start < overlap_edge_sample
            if not final and (in_overlap_region or end > live_edge_sample):
                continue
            words = _words(text)
            if not words:
                continue
            safe_texts.append(text)
            duration = max(1, end - start)
            for index, word in enumerate(words, start=1):
                word_end = start + int(duration * index / len(words))
                timings.append(_WordTiming(end_sample=word_end))

        return " ".join(safe_texts).strip(), timings, True

    def _new_words(self, candidate_words: list[str]) -> list[str]:
        if not candidate_words:
            return []
        committed = [_normalize_word(word) for word in self._committed_words]
        candidate = [_normalize_word(word) for word in candidate_words]
        if committed and len(candidate) <= len(committed) and committed[:len(candidate)] == candidate:
            return []
        if committed and candidate[:len(committed)] == committed:
            return candidate_words[len(committed):]
        max_overlap = min(len(committed), len(candidate))
        for overlap in range(max_overlap, 0, -1):
            if committed[-overlap:] == candidate[:overlap]:
                return candidate_words[overlap:]
        return candidate_words if not committed else candidate_words


def _words(text: str) -> list[str]:
    return [match.group(0).strip() for match in _WORD_RE.finditer(text) if match.group(0).strip()]


def _normalize_word(word: str) -> str:
    return "".join(ch.lower() for ch in word if ch.isalnum())


def _longest_common_word_prefix(left: list[str], right: list[str]) -> list[str]:
    stable: list[str] = []
    for left_word, right_word in zip(left, right):
        if _normalize_word(left_word) != _normalize_word(right_word):
            break
        stable.append(right_word)
    return stable


def _hold_back(words: list[str], count: int) -> list[str]:
    if count <= 0:
        return words
    if len(words) <= count:
        return []
    return words[:-count]
