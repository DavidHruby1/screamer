"""Microphone capture: device enumeration, recording, WAV encoding, RMS, auto-calibration."""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None  # type: ignore[assignment]

from src.config import DEFAULT_RMS_THRESHOLD
from src.utils import AppError, ScreamerError, log_duration

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
MIN_DURATION = 0.3


@dataclass
class AudioDevice:
    id: int
    name: str
    channels: int


@dataclass(frozen=True)
class AudioDeviceIdentity:
    id: int
    name: str


@dataclass(frozen=True)
class CaptureSnapshot:
    level_rms: float
    has_callback_data: bool
    capture_error: str | None
    device: AudioDeviceIdentity | None
    input_status: str = ""


def _require_sd():
    """Raise if sounddevice is not available."""
    if sd is None:
        raise ScreamerError(AppError.MIC_UNAVAILABLE, "sounddevice/PortAudio not available")


def list_devices() -> list[AudioDevice]:
    """Return available input devices. Raise if none found."""
    _require_sd()
    devices = sd.query_devices()
    result: list[AudioDevice] = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append(AudioDevice(id=i, name=dev["name"], channels=dev["max_input_channels"]))
    if not result:
        raise ScreamerError(AppError.MIC_UNAVAILABLE)
    return result


def default_input_device_id() -> int | None:
    """Return PortAudio's default input device ID, if one is configured."""
    _require_sd()
    default = sd.default.device

    try:
        device_id = default["input"]
    except (TypeError, KeyError, IndexError):
        device_id = default[0] if isinstance(default, (list, tuple)) else default

    if device_id is None:
        return None

    try:
        resolved_id = int(device_id)
    except (TypeError, ValueError):
        dev = sd.query_devices(device_id, "input")
        if "index" in dev:
            resolved_id = int(dev["index"])
        else:
            dev_name = str(dev["name"])
            for i, candidate in enumerate(sd.query_devices()):
                if candidate["max_input_channels"] > 0 and str(candidate["name"]) == dev_name:
                    resolved_id = i
                    break
            else:
                return None

    if resolved_id < 0:
        return None
    return resolved_id


def _clean_device_name(name: str) -> str:
    return name.removesuffix(" (Default input)").strip()


class AudioRecorder:
    def __init__(self, device_id: int | None = None, sample_rate: int = SAMPLE_RATE) -> None:
        self._device_id = device_id
        self._sample_rate = sample_rate
        self._frames: list[np.ndarray] = []
        self._stream: Any = None
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._rms_threshold: float = DEFAULT_RMS_THRESHOLD
        self._accepting_frames = False
        self._level_rms = 0.0
        self._has_callback_data = False
        self._capture_error: str | None = None
        self._device: AudioDeviceIdentity | None = None
        self._input_status = ""

    @property
    def rms_threshold(self) -> float:
        return self._rms_threshold

    @rms_threshold.setter
    def rms_threshold(self, value: float) -> None:
        self._rms_threshold = value

    @property
    def is_recording(self) -> bool:
        """True if the audio stream is currently open and recording."""
        return self._stream is not None

    def snapshot(self) -> CaptureSnapshot:
        """Return coherent capture evidence without copying audio or querying devices."""
        with self._lock:
            return CaptureSnapshot(
                self._level_rms,
                self._has_callback_data,
                self._capture_error,
                self._device,
                self._input_status,
            )

    def calibrate(self, duration: float = 2.0) -> float:
        """Record ambient noise and return a usable silence-gate threshold."""
        _require_sd()
        try:
            with log_duration(log, f"Calibration for {duration:.1f}s"):
                recording = sd.rec(
                    int(duration * self._sample_rate),
                    samplerate=self._sample_rate,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    device=self._device_id,
                )
                sd.wait()
                noise_floor = float(np.sqrt(np.mean(recording.astype(np.float64) ** 2)))
                threshold = noise_floor * 2.0
                if threshold < DEFAULT_RMS_THRESHOLD:
                    threshold = DEFAULT_RMS_THRESHOLD
                self._rms_threshold = threshold
                log.info(
                    "Calibration done: noise_floor=%.1f, threshold=%.1f", noise_floor, threshold
                )
                return threshold
        except Exception as e:
            log.warning("Calibration failed: %s; using fallback %.1f", e, DEFAULT_RMS_THRESHOLD)
            self._rms_threshold = DEFAULT_RMS_THRESHOLD
            return DEFAULT_RMS_THRESHOLD

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # type: ignore[no-untyped-def]
        level_rms = float(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))
        with self._lock:
            if not self._accepting_frames:
                return
            self._frames.append(indata.copy())
            self._level_rms = level_rms
            self._has_callback_data = True
            self._input_status = str(status) if status else ""

    def _on_stream_finished(self) -> None:
        with self._lock:
            if self._accepting_frames:
                self._capture_error = "Input stream stopped unexpectedly."
                self._accepting_frames = False

    def start(self) -> None:
        """Begin recording from the configured device."""
        _require_sd()
        with self._lock:
            self._frames.clear()
            self._level_rms = 0.0
            self._has_callback_data = False
            self._capture_error = None
            self._device = None
            self._input_status = ""
            self._accepting_frames = True
        self._start_time = time.monotonic()
        try:
            stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=CHANNELS,
                dtype=DTYPE,
                device=self._device_id,
                callback=self._callback,
                finished_callback=self._on_stream_finished,
            )
            try:
                opened_id = stream.device
                try:
                    name = str(sd.query_devices(opened_id, "input")["name"])
                    with self._lock:
                        self._device = AudioDeviceIdentity(opened_id, name)
                except (sd.PortAudioError, ValueError):
                    # Identity is optional evidence; never echo a guessed default.
                    log.warning("Could not identify the opened input device")
                stream.start()
            except Exception:
                with self._lock:
                    self._accepting_frames = False
                stream.close(ignore_errors=False)
                raise
        except Exception as e:
            with self._lock:
                self._accepting_frames = False
                self._frames.clear()
            raise ScreamerError(AppError.MIC_UNAVAILABLE, str(e)) from e
        self._stream = stream
        log.info("Recording started (device=%s)", self._device_id)

    def stop(self) -> bytes:
        """Stop recording and return 16kHz mono int16 WAV bytes.

        Raise ``ScreamerError(AppError.MIC_DISCONNECTED)`` on stream failure
        or a valid-length attempt without callback data.
        Return empty bytes if the recording is too short or silent.
        """
        if self._stream is None:
            return b""

        stream = self._stream
        self._stream = None
        with self._lock:
            self._accepting_frames = False
            capture_error = self._capture_error
        try:
            try:
                stream.stop(ignore_errors=False)
            finally:
                stream.close(ignore_errors=False)
        except Exception as e:
            with self._lock:
                self._frames.clear()
            raise ScreamerError(AppError.MIC_DISCONNECTED, str(e)) from e

        with self._lock:
            frames = self._frames
            self._frames = []
        if capture_error is not None:
            raise ScreamerError(AppError.MIC_DISCONNECTED, capture_error)

        duration = time.monotonic() - self._start_time
        if duration < MIN_DURATION:
            log.info("Recording too short (%.2fs); discarding", duration)
            return b""

        if not frames:
            raise ScreamerError(AppError.MIC_DISCONNECTED, "No microphone samples were received.")
        audio_data = np.concatenate(frames, axis=0)

        rms = float(np.sqrt(np.mean(audio_data.astype(np.float64) ** 2)))
        log.info(
            "Recording stopped: %.2fs, RMS=%.1f, threshold=%.1f", duration, rms, self._rms_threshold
        )
        if rms < self._rms_threshold:
            log.info("Below RMS threshold; discarding")
            return b""

        # Encode as WAV in memory.
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio_data.tobytes())
        wav_bytes = buf.getvalue()
        log.info("WAV encoded: %d bytes", len(wav_bytes))
        return wav_bytes


def resolve_device(
    preferred_id: int | None,
    preferred_name: str,
    *,
    devices: list[tuple[int, str]] | None = None,
) -> int | None:
    """Resolve explicit input identity without substitution; None means system default.

    Settings may supply an input-only device list to use the same policy without
    re-enumerating. A stale ID can migrate only to a unique exact-name match.
    """
    if preferred_id is None:
        return None
    if devices is None:
        devices = [(dev.id, dev.name) for dev in list_devices()]
    expected = _clean_device_name(preferred_name).casefold()
    matches = []
    current_name = None
    for device_id, name in devices:
        clean_name = _clean_device_name(name).casefold()
        if device_id == preferred_id:
            current_name = clean_name
        if expected and clean_name == expected:
            matches.append(device_id)
    if len(matches) > 1:
        raise ScreamerError(
            AppError.MIC_UNAVAILABLE, "Microphone name is ambiguous; reselect input."
        )
    if current_name is not None:
        if not expected or current_name == expected:
            return preferred_id
        raise ScreamerError(AppError.MIC_UNAVAILABLE, "Saved microphone ID/name no longer agree.")
    if len(matches) == 1:
        return matches[0]
    raise ScreamerError(
        AppError.MIC_UNAVAILABLE, "Selected microphone is unavailable; reselect input."
    )


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Available input devices:")
    try:
        for d in list_devices():
            print(f"  [{d.id}] {d.name} ({d.channels} ch)")
    except ScreamerError as e:
        print(f"  Error: {e}")
        raise SystemExit(1)

    print()
    recorder = AudioRecorder()

    print("Calibrating (2s ambient noise)...")
    threshold = recorder.calibrate(2.0)
    print(f"RMS threshold: {threshold:.1f}")

    print()
    print("Recording 3 seconds... speak now!")
    recorder.start()
    time.sleep(3)
    wav_bytes = recorder.stop()

    if wav_bytes:
        with open("test.wav", "wb") as f:
            f.write(wav_bytes)
        print(f"Wrote test.wav ({len(wav_bytes)} bytes)")
    else:
        print("No audio captured (too short or silent)")

    print()
    print("Audio module OK")
