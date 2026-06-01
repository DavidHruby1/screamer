"""Microphone capture: device enumeration, recording, WAV encoding, RMS, auto-calibration."""

from __future__ import annotations

import io
import logging
import struct
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import sounddevice as sd
except OSError:
    sd = None  # type: ignore[assignment]

from src.config import DEFAULT_RMS_THRESHOLD
from src.utils import AppError, ScreamerError

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
    device_id = default[0] if isinstance(default, (list, tuple)) else default
    if device_id is None or int(device_id) < 0:
        return None
    return int(device_id)


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

    def calibrate(self, duration: float = 2.0) -> float:
        """Record ambient noise and return a usable silence-gate threshold."""
        _require_sd()
        try:
            log.info("Calibrating RMS threshold for %.1fs...", duration)
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
            log.info("Calibration done: noise_floor=%.1f, threshold=%.1f", noise_floor, threshold)
            return threshold
        except Exception as e:
            log.warning("Calibration failed: %s; using fallback %.1f", e, DEFAULT_RMS_THRESHOLD)
            self._rms_threshold = DEFAULT_RMS_THRESHOLD
            return DEFAULT_RMS_THRESHOLD

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # type: ignore[no-untyped-def]
        if status:
            log.warning("[audio] %s", status)
        with self._lock:
            self._frames.append(indata.copy())

    def start(self) -> None:
        """Begin recording from the configured device."""
        _require_sd()
        self._frames = []
        self._start_time = time.monotonic()
        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            device=self._device_id,
            callback=self._callback,
        )
        self._stream.start()
        log.info("Recording started (device=%s)", self._device_id)

    def stop(self) -> bytes:
        """Stop recording and return 16kHz mono int16 WAV bytes.

        Raise ``ScreamerError(AppError.MIC_DISCONNECTED)`` on stream failure.
        Return empty bytes if the recording is too short or silent.
        """
        if self._stream is None:
            return b""

        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:
            self._stream = None
            raise ScreamerError(AppError.MIC_DISCONNECTED, str(e)) from e
        self._stream = None

        duration = time.monotonic() - self._start_time
        if duration < MIN_DURATION:
            log.info("Recording too short (%.2fs); discarding", duration)
            return b""

        with self._lock:
            if not self._frames:
                log.info("No frames captured; discarding")
                return b""
            audio_data = np.concatenate(self._frames, axis=0)

        rms = float(np.sqrt(np.mean(audio_data.astype(np.float64) ** 2)))
        log.info("Recording stopped: %.2fs, RMS=%.1f, threshold=%.1f", duration, rms, self._rms_threshold)
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


def resolve_device(preferred_id: int | None, preferred_name: str) -> int | None:
    """Resolve saved device ID/name, falling back to the current default input."""
    _require_sd()
    clean_name = _clean_device_name(preferred_name)

    if preferred_id is not None:
        try:
            dev = sd.query_devices(preferred_id)
            dev_name = str(dev["name"])
            if dev["max_input_channels"] > 0 and (
                not clean_name or clean_name.lower() in dev_name.lower()
            ):
                return preferred_id
            if dev["max_input_channels"] > 0:
                log.warning(
                    "Preferred device ID %d is now '%s', expected '%s'; trying name search",
                    preferred_id,
                    dev_name,
                    clean_name,
                )
        except (sd.PortAudioError, ValueError):
            log.warning("Preferred device ID %d not found; trying name search", preferred_id)

    if clean_name:
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0 and clean_name.lower() in dev["name"].lower():
                log.info("Resolved device '%s' to ID %d", clean_name, i)
                return i

    default_id = default_input_device_id()
    log.info("Using current default input device: %s", default_id)
    return default_id


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
