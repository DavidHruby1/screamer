"""Configuration persistence via QSettings, secure key storage via DPAPI, .env import, logging setup."""

from __future__ import annotations

import json
import logging
import os
import platform
from dataclasses import dataclass, field, fields
from logging.handlers import RotatingFileHandler
from urllib.parse import urlsplit

from src.utils import APP_DIR, ScreamerError, AppError

log = logging.getLogger(__name__)

DEFAULT_LLM_SYSTEM_PROMPT: str = (
    "You are a post-processing filter inside a speech-to-text dictation tool "
    "called Screamer. Your ONLY job is to clean up the raw transcription output.\n\n"
    "CRITICAL RULES — follow these exactly:\n"
    "1. You are NOT a chatbot. You are NOT having a conversation. The text you\n"
    "   receive is transcribed speech from a microphone — do NOT respond to it,\n"
    "   answer any questions in it, or engage with its content in any way.\n"
    "2. Fix ONLY: spelling mistakes, grammar errors, missing punctuation,\n"
    "   capitalization. Nothing else.\n"
    "3. Do NOT rephrase, rewrite, summarize, shorten, or \"improve\" the text.\n"
    "4. Do NOT add, remove, or change ANY words beyond fixing obvious typos.\n"
    "5. Do NOT add commentary, explanations, notes, or meta-text.\n"
    "6. If the text has no errors, return it EXACTLY as received — character\n"
    "   for character.\n"
    "7. The input may contain speech recognition errors (homophones, missing\n"
    "   words, garbled phrases). Use context to fix only clear mistakes. When\n"
    "   in doubt, leave it as-is.\n"
    "8. Output ONLY the cleaned text. No prefixes, no labels, no quotes\n"
    "   around it. The raw text and nothing else."
)

DEFAULT_RMS_THRESHOLD = 5.0

# App-level options shared by settings and tray menus. Values are canonical
# Hotkey strings (see Hotkey.to_canonical); labels come from Hotkey.to_label.
HOTKEY_OPTIONS: list[tuple[str, str]] = [
    ("ctrl+alt+key:0x20", "Ctrl+Alt+Space"),
    ("ctrl+shift+key:0x20", "Ctrl+Shift+Space"),
    ("ctrl+alt+key:0x44", "Ctrl+Alt+D"),
    ("ctrl+alt+key:0x53", "Ctrl+Alt+S"),
    ("ctrl+alt+key:0x56", "Ctrl+Alt+V"),
    ("key:0x91", "Scroll Lock"),
    ("key:0x13", "Pause"),
]

POST_KEY_OPTIONS: list[tuple[str, str]] = [
    ("none", "None"),
    ("enter", "Enter"),
    ("tab", "Tab"),
    ("space", "Space"),
    ("backspace", "Backspace"),
]


# Mouse trigger ids (our own discriminators, not Win32 constants).
MOUSE_X1 = 1       # "back" side button (XBUTTON1)
MOUSE_X2 = 2       # "forward" side button (XBUTTON2)
MOUSE_MIDDLE = 3   # middle / wheel button

_MOUSE_TOKEN_TO_CODE = {"x1": MOUSE_X1, "x2": MOUSE_X2, "middle": MOUSE_MIDDLE}
_MOUSE_CODE_TO_TOKEN = {v: k for k, v in _MOUSE_TOKEN_TO_CODE.items()}
_MOUSE_CODE_TO_LABEL = {MOUSE_X1: "Mouse Back", MOUSE_X2: "Mouse Forward", MOUSE_MIDDLE: "Mouse Middle"}

# Canonical modifier order for serialization/labels.
_MOD_ORDER = ("ctrl", "alt", "shift", "win")
_MOD_LABEL = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}

# Win32 virtual-key codes that ARE modifiers (generic + L/R variants).
# A trigger key may never be one of these.
MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})

# Map a modifier VK (as reported by the LL keyboard hook) to its canonical name.
MODIFIER_VK_TO_NAME = {
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt",
    0x5B: "win", 0x5C: "win",
}

# Keys safe to bind alone (won't eat normal typing / clicking).
SAFE_STANDALONE_KEYS = frozenset(
    set(range(0x70, 0x88))            # F1..F24
    | {0x91,                          # Scroll Lock
       0x13,                          # Pause
       0x2D,                          # Insert
       0x2C,                          # PrintScreen
       0x5D,                          # Apps / Menu
       0x90}                          # Num Lock
)

# Human-readable names for common VK codes (labels only).
_VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause",
    0x1B: "Esc", 0x20: "Space", 0x21: "Page Up", 0x22: "Page Down",
    0x23: "End", 0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right",
    0x28: "Down", 0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5D: "Menu", 0x90: "Num Lock", 0x91: "Scroll Lock",
}
_VK_NAMES.update({c: chr(c) for c in range(0x30, 0x3A)})          # 0-9
_VK_NAMES.update({c: chr(c) for c in range(0x41, 0x5B)})          # A-Z
_VK_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})      # F1..F24


def _vk_label(vk: int) -> str:
    return _VK_NAMES.get(vk, f"Key 0x{vk:02X}")


# Legacy preset keys (pre-custom-hotkey format) -> (modifier string, VK code).
_LEGACY_HOTKEYS = {
    "ctrl_alt_space": ("ctrl+alt", 0x20),
    "ctrl_shift_space": ("ctrl+shift", 0x20),
    "ctrl_alt_d": ("ctrl+alt", 0x44),
    "ctrl_alt_s": ("ctrl+alt", 0x53),
    "ctrl_alt_v": ("ctrl+alt", 0x56),
    "scroll_lock": ("", 0x91),
    "pause": ("", 0x13),
}


@dataclass(frozen=True)
class Hotkey:
    """A push-to-talk binding: a set of modifiers + a single key or mouse trigger.

    ``mods`` is a subset of {"ctrl","alt","shift","win"}. ``kind`` is "key" or
    "mouse". ``code`` is a Win32 virtual-key code (kind="key") or one of the
    ``MOUSE_*`` ids (kind="mouse").
    """

    mods: frozenset
    kind: str
    code: int

    def to_canonical(self) -> str:
        prefix = "".join(f"{m}+" for m in _MOD_ORDER if m in self.mods)
        if self.kind == "mouse":
            token = _MOUSE_CODE_TO_TOKEN.get(self.code, str(self.code))
            return f"{prefix}mouse:{token}"
        return f"{prefix}key:0x{self.code:02X}"

    def to_label(self) -> str:
        prefix = "".join(f"{_MOD_LABEL[m]}+" for m in _MOD_ORDER if m in self.mods)
        if self.kind == "mouse":
            return prefix + _MOUSE_CODE_TO_LABEL.get(self.code, f"Mouse {self.code}")
        return prefix + _vk_label(self.code)

    def validate(self) -> str | None:
        """Return an error message if this binding is unsafe, else None."""
        if self.kind == "mouse":
            if self.code not in _MOUSE_CODE_TO_TOKEN:
                return "Only the side or middle mouse buttons can be used."
            return None
        if self.code in MODIFIER_VKS:
            return "Pick a non-modifier key, then add Ctrl/Alt/Shift as modifiers."
        if self.code in SAFE_STANDALONE_KEYS:
            return None
        if not self.mods:
            return "Add a modifier (Ctrl/Alt/Shift) or choose a function key."
        return None

    @classmethod
    def parse(cls, value: str) -> "Hotkey | None":
        """Parse a canonical string or a legacy preset key. None if invalid."""
        if not value:
            return None
        if value in _LEGACY_HOTKEYS:
            mod_str, code = _LEGACY_HOTKEYS[value]
            mods = frozenset(p for p in mod_str.split("+") if p)
            return cls(mods, "key", code)

        parts = value.split("+")
        trigger = parts[-1]
        mod_parts = parts[:-1]
        if any(m not in _MOD_ORDER for m in mod_parts):
            return None
        mods = frozenset(mod_parts)

        if trigger.startswith("mouse:"):
            token = trigger[len("mouse:"):]
            if token not in _MOUSE_TOKEN_TO_CODE:
                return None
            return cls(mods, "mouse", _MOUSE_TOKEN_TO_CODE[token])
        if trigger.startswith("key:"):
            try:
                code = int(trigger[len("key:"):], 16)
            except ValueError:
                return None
            return cls(mods, "key", code)
        return None


@dataclass(frozen=True)
class ProviderConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    custom_headers: str = ""

    @property
    def has_any_value(self) -> bool:
        return bool(self.api_key or self.base_url or self.model or self.custom_headers)

    @property
    def is_complete(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    @property
    def is_groq(self) -> bool:
        return urlsplit(self.base_url).hostname == "api.groq.com"


@dataclass(frozen=True)
class FallbackProviderConfig:
    enabled: bool = False
    provider: ProviderConfig = field(default_factory=ProviderConfig)

    @property
    def is_complete(self) -> bool:
        return self.enabled and self.provider.is_complete


@dataclass(frozen=True)
class ConfigValidationIssue:
    message: str
    tab_index: int = 0


@dataclass
class AppConfig:
    hotkey: str = "ctrl+alt+key:0x20"
    recording_mode: str = "hold"  # "hold" | "toggle"
    post_type_key: str = "none"  # "none" | "enter" | "tab" | "space" | "backspace"
    start_with_windows: bool = False
    audio_device_id: int | None = None
    audio_device_name: str = ""
    rms_threshold: float = DEFAULT_RMS_THRESHOLD
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

    def stt_provider(self) -> ProviderConfig:
        return ProviderConfig(
            api_key=self.stt_api_key,
            base_url=self.stt_base_url,
            model=self.stt_model,
            custom_headers=self.stt_custom_headers,
        )

    def stt_fallback_provider(self) -> FallbackProviderConfig:
        return FallbackProviderConfig(
            enabled=self.stt_fallback_enabled,
            provider=ProviderConfig(
                api_key=self.stt_fallback_api_key,
                base_url=self.stt_fallback_base_url,
                model=self.stt_fallback_model,
                custom_headers=self.stt_fallback_custom_headers,
            ),
        )

    def llm_provider(self) -> ProviderConfig:
        return ProviderConfig(
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
            model=self.llm_model,
            custom_headers=self.llm_custom_headers,
        )

    def llm_fallback_provider(self) -> FallbackProviderConfig:
        return FallbackProviderConfig(
            enabled=self.llm_fallback_enabled,
            provider=ProviderConfig(
                api_key=self.llm_fallback_api_key,
                base_url=self.llm_fallback_base_url,
                model=self.llm_fallback_model,
                custom_headers=self.llm_fallback_custom_headers,
            ),
        )


# Fields that contain secret API keys and must go through DPAPI.
_SECRET_FIELDS = frozenset({
    "stt_api_key",
    "stt_fallback_api_key",
    "llm_api_key",
    "llm_fallback_api_key",
})

# DPAPI entropy string bound to this application.
_ENTROPY = b"screamer-dpapi-v1"


# ---------------------------------------------------------------------------
# DPAPI helpers (Windows-only, guarded at runtime)
# ---------------------------------------------------------------------------

def _dpapi_available() -> bool:
    return platform.system() == "Windows"


def _dpapi_crypt(data: bytes, protect: bool, errmsg: str) -> bytes:
    """Run a DPAPI Protect/Unprotect call over *data*, bound to the app entropy.

    *protect* selects ``CryptProtectData`` (True) or ``CryptUnprotectData`` (False).
    Raises ``ScreamerError(KEY_STORAGE_FAILED)`` on failure.
    """
    if not _dpapi_available():
        raise ScreamerError(AppError.UNSUPPORTED_PLATFORM, "DPAPI requires Windows")

    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    blob_in = DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_entropy = DATA_BLOB(len(_ENTROPY), ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY)))
    blob_out = DATA_BLOB()

    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(
        ctypes.byref(blob_in),
        None,
        ctypes.byref(blob_entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise ScreamerError(AppError.KEY_STORAGE_FAILED, errmsg)

    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    kernel32.LocalFree(blob_out.pbData)
    return result


def _dpapi_encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* with Windows DPAPI. Returns hex-encoded blob string."""
    return _dpapi_crypt(plaintext.encode("utf-8"), protect=True, errmsg="DPAPI encrypt failed").hex()


def _dpapi_decrypt(hex_blob: str) -> str:
    """Decrypt a hex-encoded DPAPI blob. Returns plaintext string."""
    return _dpapi_crypt(bytes.fromhex(hex_blob), protect=False, errmsg="DPAPI decrypt failed").decode("utf-8")


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
            elif key == "audio_device_id":
                if val in (None, ""):
                    val = None
                else:
                    try:
                        val = int(val)
                    except (ValueError, TypeError):
                        continue
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
    parsed_hotkey = Hotkey.parse(cfg.hotkey)
    if parsed_hotkey is None or parsed_hotkey.validate() is not None:
        cfg.hotkey = "ctrl+alt+key:0x20"
    else:
        cfg.hotkey = parsed_hotkey.to_canonical()
    if cfg.post_type_key not in {key for key, _label in POST_KEY_OPTIONS}:
        cfg.post_type_key = "none"
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


def parse_custom_headers(custom_headers: str) -> dict[str, str]:
    """Parse provider custom headers as a JSON object of string-ish values."""
    if not custom_headers:
        return {}

    parsed = json.loads(custom_headers)
    if not isinstance(parsed, dict):
        raise ValueError("Custom headers must be a JSON object")

    return {str(key): str(value) for key, value in parsed.items()}


def validate_config(cfg: AppConfig) -> list[ConfigValidationIssue]:
    """Return all startup/settings validation issues for the current config."""
    issues: list[ConfigValidationIssue] = []

    parsed_hotkey = Hotkey.parse(cfg.hotkey)
    if parsed_hotkey is None or parsed_hotkey.validate() is not None:
        issues.append(ConfigValidationIssue("Choose a valid global hotkey.", 0))

    stt = cfg.stt_provider()
    stt_fallback = cfg.stt_fallback_provider()
    if stt.has_any_value and not stt.is_complete:
        issues.append(
            ConfigValidationIssue("Primary STT requires an API key, base URL, and model.", 1)
        )
    if stt_fallback.enabled and not stt_fallback.provider.is_complete:
        issues.append(
            ConfigValidationIssue("Fallback STT requires an API key, base URL, and model.", 1)
        )
    if not stt.is_complete and not stt_fallback.is_complete:
        issues.append(
            ConfigValidationIssue("Configure a complete primary or fallback STT provider.", 1)
        )

    llm = cfg.llm_provider()
    llm_fallback = cfg.llm_fallback_provider()
    if cfg.llm_enabled:
        if llm.has_any_value and not llm.is_complete:
            issues.append(
                ConfigValidationIssue("Primary LLM requires an API key, base URL, and model.", 2)
            )
        if llm_fallback.enabled and not llm_fallback.provider.is_complete:
            issues.append(
                ConfigValidationIssue("Fallback LLM requires an API key, base URL, and model.", 2)
            )
        if not llm.is_complete and not llm_fallback.is_complete:
            issues.append(
                ConfigValidationIssue(
                    "AI rewrite requires a complete primary or fallback LLM provider.", 2
                )
            )

    for headers, label, tab_index in (
        (cfg.stt_custom_headers, "Primary STT", 1),
        (cfg.stt_fallback_custom_headers, "Fallback STT", 1),
        (cfg.llm_custom_headers, "Primary LLM", 2),
        (cfg.llm_fallback_custom_headers, "Fallback LLM", 2),
    ):
        try:
            parse_custom_headers(headers)
        except (json.JSONDecodeError, ValueError) as e:
            issues.append(ConfigValidationIssue(f"{label} custom headers are invalid: {e}", tab_index))

    return issues


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
        "LLM_FALLBACK_HEADERS": "llm_fallback_custom_headers",
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
