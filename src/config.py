"""Configuration persistence via QSettings, secure key storage via DPAPI, .env import, logging setup."""

from __future__ import annotations

import json
import logging
import os
import platform
from dataclasses import dataclass, field, fields
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.utils import APP_DIR, APP_NAME, ScreamerError, AppError

log = logging.getLogger(__name__)

DEFAULT_LLM_SYSTEM_PROMPT: str = (
    "You are a text correction assistant. Fix grammar, spelling, and punctuation "
    "errors in the input text. Preserve the original meaning and tone. "
    "Return only the corrected text with no explanations."
)


@dataclass
class AppConfig:
    hotkey: str = "scroll_lock"
    recording_mode: str = "hold"  # "hold" | "toggle"
    post_type_key: str = "none"  # "none" | "enter" | "tab" | "space" | "backspace"
    audio_device_id: int | None = None
    audio_device_name: str = ""
    rms_threshold: float = 50.0
    # STT primary
    stt_api_key: str = ""
    stt_base_url: str = ""
    stt_model: str = ""
    stt_language: str = ""
    stt_custom_headers: str = ""
    # STT fallback
    stt_fallback_enabled: bool = False
    stt_fallback_api_key: str = ""
    stt_fallback_base_url: str = ""
    stt_fallback_model: str = ""
    stt_fallback_custom_headers: str = ""
    # LLM
    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_custom_headers: str = ""
    llm_system_prompt: str = DEFAULT_LLM_SYSTEM_PROMPT
    # LLM fallback
    llm_fallback_enabled: bool = False
    llm_fallback_api_key: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_custom_headers: str = ""


# Fields that contain secret API keys and must go through DPAPI.
_SECRET_FIELDS = frozenset({
    "stt_api_key",
    "stt_fallback_api_key",
    "llm_api_key",
    "llm_fallback_api_key",
})

# All non-secret field names.
_PLAIN_FIELDS = [f.name for f in fields(AppConfig) if f.name not in _SECRET_FIELDS]

# DPAPI entropy string bound to this application.
_ENTROPY = b"screamer-dpapi-v1"


# ---------------------------------------------------------------------------
# DPAPI helpers (Windows-only, guarded at runtime)
# ---------------------------------------------------------------------------

def _dpapi_available() -> bool:
    return platform.system() == "Windows"


def _dpapi_encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* with Windows DPAPI. Returns hex-encoded blob string."""
    if not _dpapi_available():
        raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "DPAPI requires Windows")

    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    data_in = plaintext.encode("utf-8")
    entropy_in = _ENTROPY

    blob_in = DATA_BLOB(len(data_in), ctypes.create_string_buffer(data_in, len(data_in)))
    blob_entropy = DATA_BLOB(len(entropy_in), ctypes.create_string_buffer(entropy_in, len(entropy_in)))
    blob_out = DATA_BLOB()

    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        ctypes.byref(blob_entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise ScreamerError(AppError.KEY_STORAGE_FAILED, "DPAPI encrypt failed")

    encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    kernel32.LocalFree(blob_out.pbData)
    return encrypted.hex()


def _dpapi_decrypt(hex_blob: str) -> str:
    """Decrypt a hex-encoded DPAPI blob. Returns plaintext string."""
    if not _dpapi_available():
        raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "DPAPI requires Windows")

    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    data_in = bytes.fromhex(hex_blob)
    entropy_in = _ENTROPY

    blob_in = DATA_BLOB(len(data_in), ctypes.create_string_buffer(data_in, len(data_in)))
    blob_entropy = DATA_BLOB(len(entropy_in), ctypes.create_string_buffer(entropy_in, len(entropy_in)))
    blob_out = DATA_BLOB()

    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        ctypes.byref(blob_entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise ScreamerError(AppError.KEY_STORAGE_FAILED, "DPAPI decrypt failed")

    decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    kernel32.LocalFree(blob_out.pbData)
    return decrypted.decode("utf-8")


# ---------------------------------------------------------------------------
# QSettings helpers
# ---------------------------------------------------------------------------

def _get_qsettings():
    """Return a QSettings instance for the app. Import PySide6 lazily."""
    from PySide6.QtCore import QSettings

    os.makedirs(APP_DIR, exist_ok=True)
    ini_path = os.path.join(APP_DIR, "settings.ini")
    settings = QSettings(ini_path, QSettings.Format.IniFormat)
    return settings


def _save_secrets(cfg: AppConfig) -> None:
    """Persist secret fields via DPAPI to APP_DIR/keys.enc."""
    if not _dpapi_available():
        log.debug("DPAPI unavailable; skipping secret persistence")
        return

    os.makedirs(APP_DIR, exist_ok=True)
    blob = {}
    for name in _SECRET_FIELDS:
        val = getattr(cfg, name)
        if val:
            blob[name] = _dpapi_encrypt(val)
    path = os.path.join(APP_DIR, "keys.enc")
    with open(path, "w") as f:
        json.dump(blob, f)


def _load_secrets(cfg: AppConfig) -> None:
    """Load secret fields from DPAPI blob, backfilling empty fields only."""
    if not _dpapi_available():
        return

    path = os.path.join(APP_DIR, "keys.enc")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            blob = json.load(f)
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read keys.enc; ignoring")
        return

    for name, hex_val in blob.items():
        if name in _SECRET_FIELDS and not getattr(cfg, name):
            try:
                setattr(cfg, name, _dpapi_decrypt(hex_val))
            except ScreamerError:
                log.warning("Failed to decrypt %s; skipping", name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config() -> AppConfig:
    """Load QSettings + DPAPI. Unknown keys get field defaults."""
    settings = _get_qsettings()
    cfg = AppConfig()

    # Load plain fields from QSettings.
    known = {f.name for f in fields(AppConfig)}
    for key in settings.allKeys():
        if key in known:
            val = settings.value(key)
            current = getattr(cfg, key)
            # Coerce types to match dataclass fields.
            if isinstance(current, bool):
                val = str(val).lower() in ("true", "1", "yes")
            elif isinstance(current, int) and val is not None:
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    continue
            elif isinstance(current, float) and val is not None:
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue
            setattr(cfg, key, val)

    _load_secrets(cfg)
    return cfg


def save_config(cfg: AppConfig) -> None:
    """Persist to QSettings + DPAPI. api_key fields go through DPAPI."""
    settings = _get_qsettings()
    for f in fields(AppConfig):
        if f.name in _SECRET_FIELDS:
            continue
        settings.setValue(f.name, getattr(cfg, f.name))
    settings.sync()
    _save_secrets(cfg)


def reset_config() -> AppConfig:
    """Fresh AppConfig with all defaults. Does not write disk."""
    return AppConfig()


def import_from_env(cfg: AppConfig) -> AppConfig:
    """Read .env at cwd; backfill ONLY empty str fields. No-op if no .env file."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        log.debug("python-dotenv not installed; skipping .env import")
        return cfg

    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return cfg

    env = dotenv_values(env_path)

    # Mapping from .env variable names to AppConfig field names.
    env_map = {
        "STT_API_KEY": "stt_api_key",
        "STT_BASE_URL": "stt_base_url",
        "STT_MODEL": "stt_model",
        "STT_LANGUAGE": "stt_language",
        "STT_HEADERS": "stt_custom_headers",
        "STT_FALLBACK_API_KEY": "stt_fallback_api_key",
        "STT_FALLBACK_BASE_URL": "stt_fallback_base_url",
        "STT_FALLBACK_MODEL": "stt_fallback_model",
        "STT_FALLBACK_HEADERS": "stt_fallback_custom_headers",
        "LLM_API_KEY": "llm_api_key",
        "LLM_BASE_URL": "llm_base_url",
        "LLM_MODEL": "llm_model",
        "LLM_HEADERS": "llm_custom_headers",
        "LLM_FALLBACK_API_KEY": "llm_fallback_api_key",
        "LLM_FALLBACK_BASE_URL": "llm_fallback_base_url",
        "LLM_FALLBACK_MODEL": "llm_fallback_model",
    }

    for env_name, field_name in env_map.items():
        val = env.get(env_name, "")
        if val and not getattr(cfg, field_name):
            setattr(cfg, field_name, val)

    return cfg


def setup_logging(debug: bool = False) -> None:
    """Rotating file at APP_DIR/screamer.log. Never log api_key values.
    Never log transcripts unless debug=True."""
    os.makedirs(APP_DIR, exist_ok=True)
    log_path = os.path.join(APP_DIR, "screamer.log")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # File handler: 2 MB max, keep 3 backups.
    fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler.
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    log.info("Logging initialized (debug=%s)", debug)


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"APP_DIR: {APP_DIR}")
    print()

    cfg = load_config()
    print("Loaded config defaults:")
    for f in fields(AppConfig):
        print(f"  {f.name} = {getattr(cfg, f.name)}")

    print()

    # DPAPI roundtrip test (Windows only).
    if _dpapi_available():
        test_val = "test-secret-key-12345"
        enc = _dpapi_encrypt(test_val)
        dec = _dpapi_decrypt(enc)
        assert dec == test_val, f"DPAPI roundtrip failed: {dec!r} != {test_val!r}"
        print(f"DPAPI roundtrip OK: encrypted {len(enc)} chars, decrypted matches")
    else:
        print("DPAPI not available (non-Windows); skipping roundtrip test")

    print()

    # .env import test.
    cfg2 = import_from_env(cfg)
    print("After import_from_env (may be no-op):")
    for f_name in _SECRET_FIELDS:
        val = getattr(cfg2, f_name)
        print(f"  {f_name} = {'***' if val else '(empty)'}")

    print()
    print("Config module OK")
