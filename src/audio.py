"""Microphone capture: device enumeration, recording, WAV encoding, RMS, auto-calibration."""

from __future__ import annotations

import ctypes
import io
import logging
import platform
import threading
import time
import uuid
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
# Speech often ends a moment after the hotkey is released; keep capturing briefly.
RELEASE_TAIL = 0.25


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
        self._stop_requested = False
        self._level_rms = 0.0
        self._has_callback_data = False
        self._capture_error: str | None = None
        self._device: AudioDeviceIdentity | None = None
        self._input_status = ""
        self._trim_silence = True
        self._retain_frames = True

    @property
    def trim_silence(self) -> bool:
        return self._trim_silence

    @trim_silence.setter
    def trim_silence(self, value: bool) -> None:
        self._trim_silence = bool(value)

    @property
    def retain_frames(self) -> bool:
        """When False, only the live level is tracked and ``stop()`` returns no audio."""
        return self._retain_frames

    @retain_frames.setter
    def retain_frames(self, value: bool) -> None:
        self._retain_frames = bool(value)

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
        """Record ambient noise and return a usable silence-gate threshold.

        Raise ``ScreamerError(AppError.MIC_UNAVAILABLE)`` if recording fails; the
        current threshold is left unchanged.
        """
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
                threshold = max(threshold, DEFAULT_RMS_THRESHOLD)
                self._rms_threshold = threshold
                log.info(
                    "Calibration done: noise_floor=%.1f, threshold=%.1f", noise_floor, threshold
                )
                return threshold
        except Exception as e:
            log.warning("Calibration failed: %s", e)
            raise ScreamerError(AppError.MIC_UNAVAILABLE, str(e)) from e

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # type: ignore[no-untyped-def]
        level_rms = float(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))
        with self._lock:
            if not self._accepting_frames:
                return
            if self._retain_frames:
                self._frames.append(indata.copy())
            self._level_rms = level_rms
            self._has_callback_data = True
            self._input_status = str(status) if status else ""

    def _on_stream_finished(self) -> None:
        with self._lock:
            if self._accepting_frames and not self._stop_requested:
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
            self._stop_requested = False
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

    def stop(self, tail: float = 0.0) -> bytes:
        """Stop recording and return 16kHz mono int16 WAV bytes.

        ``tail`` keeps capturing for that many seconds first. Samples the backend
        drains while stopping are kept, so the end of speech is not cut off.

        Raise ``ScreamerError(AppError.MIC_DISCONNECTED)`` on stream failure
        or a valid-length attempt without callback data.
        Return empty bytes if the recording is too short or silent.
        """
        if self._stream is None:
            return b""

        stream = self._stream
        self._stream = None
        if tail > 0:
            time.sleep(tail)
        with self._lock:
            self._stop_requested = True
        try:
            try:
                stream.stop(ignore_errors=False)
            finally:
                with self._lock:
                    self._accepting_frames = False
                stream.close(ignore_errors=False)
        except Exception as e:
            with self._lock:
                self._frames.clear()
            raise ScreamerError(AppError.MIC_DISCONNECTED, str(e)) from e

        with self._lock:
            frames = self._frames
            self._frames = []
            capture_error = self._capture_error
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

        if self._trim_silence:
            trimmed = trim_silence(audio_data, self._sample_rate, min_threshold=self._rms_threshold)
            log.info(
                "Silence trim: %.2fs -> %.2fs",
                len(audio_data) / self._sample_rate,
                len(trimmed) / self._sample_rate,
            )
            audio_data = trimmed

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


def trim_silence(
    audio: np.ndarray,
    sample_rate: int,
    *,
    min_threshold: float,
    pad_seconds: float = 0.3,
    window_seconds: float = 0.02,
) -> np.ndarray:
    """Return ``audio`` without leading/trailing silence; the middle is never touched.

    Speech is any window whose RMS exceeds ``max(min_threshold, 3 * noise_floor)``,
    where the noise floor is the 10th percentile of window RMS. ``pad_seconds`` of
    audio is kept around the first and last speech window. If no window qualifies
    the input is returned unchanged (the caller's RMS gate decides discarding).
    Accepts ``(N,)`` or ``(N, channels)`` and returns a slice of the same shape.
    """
    n = len(audio)
    if n == 0:
        return audio
    window = max(1, round(sample_rate * window_seconds))
    power = np.mean(audio.reshape(n, -1).astype(np.float64) ** 2, axis=1)
    starts = np.arange(0, n, window)
    counts = np.diff(np.append(starts, n))
    rms = np.sqrt(np.add.reduceat(power, starts) / counts)
    threshold = max(min_threshold, float(np.percentile(rms, 10)) * 3.0)
    loud = np.flatnonzero(rms > threshold)
    if loud.size == 0:
        return audio
    pad = round(pad_seconds * sample_rate)
    begin = max(0, int(starts[loud[0]]) - pad)
    end = min(n, int(starts[loud[-1]] + counts[loud[-1]]) + pad)
    return audio[begin:end]


# ---------------------------------------------------------------------------
# System output mute (Windows Core Audio via raw ctypes COM)
# ---------------------------------------------------------------------------


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    # GUID's in-memory layout is UUID.bytes_le (little-endian first three fields).
    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


_CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
_CLSCTX_ALL = 0x17
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = 0x80010106 - (1 << 32)  # as signed HRESULT
_E_RENDER = 0
_E_CONSOLE = 0

# COM vtable slots. Every interface starts with IUnknown:
#   0 QueryInterface, 1 AddRef, 2 Release
# IMMDeviceEnumerator: 3 EnumAudioEndpoints, 4 GetDefaultAudioEndpoint
# IMMDevice: 3 Activate
# IAudioEndpointVolume: 3 RegisterControlChangeNotify, 4 Unregister..., 5 GetChannelCount,
#   6-13 master/channel volume get/set, 14 SetMute, 15 GetMute
_RELEASE = 2
_GET_DEFAULT_AUDIO_ENDPOINT = 4
_ACTIVATE = 3
_SET_MUTE = 14
_GET_MUTE = 15


def _vtable_fn(obj: ctypes.c_void_p, index: int, restype: Any, *argtypes: Any) -> Any:
    """Return a callable for vtable slot ``index`` of COM pointer ``obj`` (``this`` first)."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)  # type: ignore[attr-defined]
    return proto(vtable[index])


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


_HRESULT = ctypes.c_long  # 32-bit on Windows
_PTR = ctypes.c_void_p


class SystemAudioMute:
    """Best-effort mute of the default render endpoint while recording.

    ``mute()`` mutes only if output was not already muted; ``restore()`` unmutes
    only what we muted and is idempotent. Failures are logged, never raised.
    No-op off Windows.
    """

    def __init__(self) -> None:
        self._muted_by_us = False

    def mute(self) -> None:
        if self._muted_by_us or platform.system() != "Windows":
            return
        try:
            self._muted_by_us = self._with_endpoint(self._mute_if_unmuted)
        except Exception as e:  # noqa: BLE001 - best effort; never break recording
            log.warning("Could not mute system audio: %s", e)

    def restore(self) -> None:
        if not self._muted_by_us:
            return
        self._muted_by_us = False
        if platform.system() != "Windows":
            return
        try:
            self._with_endpoint(lambda ep: self._set_mute(ep, False))
        except Exception as e:  # noqa: BLE001 - best effort; never break recording
            log.warning("Could not restore system audio: %s", e)

    @staticmethod
    def _set_mute(endpoint: ctypes.c_void_p, muted: bool) -> None:
        # HRESULT SetMute(BOOL bMute, LPCGUID pguidEventContext)
        set_mute = _vtable_fn(endpoint, _SET_MUTE, _HRESULT, ctypes.c_int, _PTR)
        _check(set_mute(endpoint, int(muted), None), "SetMute")

    @classmethod
    def _mute_if_unmuted(cls, endpoint: ctypes.c_void_p) -> bool:
        # HRESULT GetMute(BOOL *pbMute)
        muted = ctypes.c_int(0)
        get_mute = _vtable_fn(endpoint, _GET_MUTE, _HRESULT, ctypes.POINTER(ctypes.c_int))
        _check(get_mute(endpoint, ctypes.byref(muted)), "GetMute")
        if muted.value:
            return False
        cls._set_mute(endpoint, True)
        return True

    @staticmethod
    def _with_endpoint(action: Any) -> Any:
        """Run ``action(IAudioEndpointVolume*)`` on the default console render device."""
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        ole32.CoInitializeEx.restype = _HRESULT
        ole32.CoInitializeEx.argtypes = [_PTR, ctypes.c_uint32]
        ole32.CoCreateInstance.restype = _HRESULT
        ole32.CoCreateInstance.argtypes = [_PTR, _PTR, ctypes.c_uint32, _PTR, _PTR]
        init_hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        if init_hr != _RPC_E_CHANGED_MODE:  # S_FALSE / changed mode are fine
            _check(init_hr, "CoInitializeEx")
        acquired: list[ctypes.c_void_p] = []
        try:
            enumerator = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(_guid(_CLSID_MMDeviceEnumerator)),
                None,
                _CLSCTX_ALL,
                ctypes.byref(_guid(_IID_IMMDeviceEnumerator)),
                ctypes.byref(enumerator),
            )
            _check(hr, "CoCreateInstance(MMDeviceEnumerator)")
            acquired.append(enumerator)

            # HRESULT GetDefaultAudioEndpoint(EDataFlow, ERole, IMMDevice **)
            device = ctypes.c_void_p()
            get_default = _vtable_fn(
                enumerator, _GET_DEFAULT_AUDIO_ENDPOINT, _HRESULT, ctypes.c_int, ctypes.c_int, _PTR
            )
            _check(
                get_default(enumerator, _E_RENDER, _E_CONSOLE, ctypes.byref(device)),
                "GetDefaultAudioEndpoint",
            )
            acquired.append(device)

            # HRESULT Activate(REFIID, DWORD dwClsCtx, PROPVARIANT *pActivationParams, void **)
            endpoint = ctypes.c_void_p()
            activate = _vtable_fn(device, _ACTIVATE, _HRESULT, _PTR, ctypes.c_uint32, _PTR, _PTR)
            hr = activate(
                device,
                ctypes.byref(_guid(_IID_IAudioEndpointVolume)),
                _CLSCTX_ALL,
                None,
                ctypes.byref(endpoint),
            )
            _check(hr, "IMMDevice::Activate(IAudioEndpointVolume)")
            acquired.append(endpoint)
            return action(endpoint)
        finally:
            for obj in reversed(acquired):
                if obj.value:
                    _vtable_fn(obj, _RELEASE, ctypes.c_ulong)(obj)
            if init_hr >= 0:  # balance S_OK / S_FALSE; not RPC_E_CHANGED_MODE
                ole32.CoUninitialize()


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
    try:
        threshold = recorder.calibrate(2.0)
        print(f"RMS threshold: {threshold:.1f}")
    except ScreamerError as e:
        print(f"  Calibration failed: {e}; using {recorder.rms_threshold:.1f}")

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
