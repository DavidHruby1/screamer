# Screamer — Implementation Plan

> Full architecture rationale, line budgets, threading model, and design decisions are in `docs/PLAN.md`. Read that first.

This document defines a two-phase build sequence with explicit public API contracts, acceptance gates, and a mandatory review step between phases.

---

## File Layout

All Python modules live under the `src/` package. `requirements.txt` lives at repo root.

```
src/
├── __init__.py          (empty)
├── main.py
├── settings_dialog.py
├── config.py
├── audio.py
├── hotkey.py
├── stt.py
├── rewrite.py
├── injector.py
├── icons.py
├── utils.py
└── startup.py
requirements.txt         (repo root)
```

---

## Public API Contracts

Phase 2 must import Phase 1 modules **only** through these exports. No internal helpers, private attrs, or module-level state outside this list.

### utils.py

```python
class AppError(Enum):
    MIC_UNAVAILABLE = "No microphone detected. Check your audio settings."
    MIC_DISCONNECTED = "Microphone disconnected during recording."
    STT_FAILED = "Transcription failed. Check your API key and internet."
    STT_FALLBACK_USED = "Primary STT failed. Used fallback provider."
    LLM_FAILED = "AI rewrite failed. Using raw transcription."
    NETWORK_ERROR = "Network error. Please check your connection."
    NO_SPEECH = "No speech detected. Try speaking louder or closer."
    INJECTION_FAILED = "Could not type text. Focus may have changed."
    HOTKEY_CONFLICT = "Hotkey conflict. Choose a different hotkey."
    UNSUPPORTED_PLATFORM = "This feature is only available on Windows."
    KEY_STORAGE_FAILED = "Could not save or load API keys securely."
    STARTUP_REGISTRATION_FAILED = "Could not update Windows startup setting."

class ScreamerError(Exception):
    def __init__(self, code: AppError, detail: str | None = None): ...

class SignalBridge(QObject):
    hotkey_pressed = Signal()
    hotkey_released = Signal()
    error_occurred = Signal(AppError)

@dataclass
class PipelineResult:
    text: str
    warnings: list[AppError]

APP_NAME: str           # "Screamer"
APP_DIR: str            # resolved %LOCALAPPDATA%/Screamer/
```

### config.py

DEFAULT_LLM_SYSTEM_PROMPT: str = (
    "You are a text correction assistant. Fix grammar, spelling, and punctuation "
    "errors in the input text. Preserve the original meaning and tone. "
    "Return only the corrected text with no explanations."
)

```python
DEFAULT_RMS_THRESHOLD: float = 5.0

```python
MOUSE_X1 = 1; MOUSE_X2 = 2; MOUSE_MIDDLE = 3   # mouse trigger ids

HOTKEY_OPTIONS: list[tuple[str, str]]  # (canonical_string, display_label) preset pairs
SAFE_STANDALONE_KEYS: frozenset[int]   # VKs bindable without a modifier (F-keys, locks, etc.)
MODIFIER_VK_TO_NAME: dict[int, str]    # LL-hook modifier VK → "ctrl"/"alt"/"shift"/"win"

@dataclass(frozen=True)
class Hotkey:
    """Modifiers + a single key/mouse trigger. Serialized to one canonical string."""
    mods: frozenset   # subset of {"ctrl","alt","shift","win"}
    kind: str         # "key" | "mouse"
    code: int         # Win32 VK (kind="key") or a MOUSE_* id (kind="mouse")

    def to_canonical(self) -> str: ...   # "ctrl+alt+key:0x20", "ctrl+mouse:x1", "key:0x91"
    def to_label(self) -> str: ...       # "Ctrl+Alt+Space", "Mouse Back"
    def validate(self) -> str | None: ...  # error message if unsafe, else None
    @classmethod
    def parse(cls, value: str) -> "Hotkey | None": ...  # canonical OR legacy preset key

@dataclass(frozen=True)
class ProviderConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    custom_headers: str = ""

@dataclass(frozen=True)
class FallbackProviderConfig:
    enabled: bool = False
    provider: ProviderConfig = field(default_factory=ProviderConfig)

@dataclass(frozen=True)
class ConfigValidationIssue:
    message: str
    tab_index: int = 0

@dataclass
class AppConfig:
    hotkey: str = "ctrl+alt+key:0x20"     # canonical Hotkey string (see Hotkey.parse)
    recording_mode: str = "hold"          # "hold" | "toggle"
    post_type_key: str = "none"           # "none" | "enter" | "tab" | "space" | "backspace"
    start_with_windows: bool = False
    audio_device_id: int | None = None
    audio_device_name: str = ""
    rms_threshold: float = 5.0
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

    def stt_provider(self) -> ProviderConfig: ...
    def stt_fallback_provider(self) -> FallbackProviderConfig: ...
    def llm_provider(self) -> ProviderConfig: ...
    def llm_fallback_provider(self) -> FallbackProviderConfig: ...

def load_config() -> AppConfig: ...
    """Load QSettings + DPAPI. Unknown keys get field defaults."""

def save_config(cfg: AppConfig) -> None: ...
    """Persist to QSettings + DPAPI. api_key and custom-header fields go through DPAPI."""

def reset_config() -> AppConfig: ...
    """Fresh AppConfig with all defaults. Does not write disk."""

def import_from_env(cfg: AppConfig) -> AppConfig: ...
    """Read .env at cwd; backfill ONLY empty str fields. No-op if no .env file."""

def setup_logging(debug: bool = False) -> None: ...
    """Rotating file at APP_DIR/screamer.log. Never log api_key values.
    Never log transcripts unless debug=True."""

def parse_custom_headers(custom_headers: str) -> dict[str, str]: ...
    """Parse provider custom headers as a JSON object of string-ish values."""

def validate_config(cfg: AppConfig) -> list[ConfigValidationIssue]: ...
    """Return all startup/settings validation issues for the current config."""
```

### audio.py

```python
@dataclass
class AudioDevice:
    id: int; name: str; channels: int

def list_devices() -> list[AudioDevice]: ...
    """Raise ScreamerError(AppError.MIC_UNAVAILABLE) if none found."""

class AudioRecorder:
    def __init__(self, device_id: int | None = None, sample_rate: int = 16000): ...
    @property
    def rms_threshold(self) -> float: ...
    def calibrate(self, duration: float = 2.0) -> float: ...
        """Record ambient noise, return noise_floor * 2.0. Fallback: 50."""
    def start(self) -> None: ...
    def stop(self) -> bytes: ...
        """Return 16kHz mono int16 WAV bytes. Raise ScreamerError(AppError.MIC_DISCONNECTED) on failure."""

def resolve_device(preferred_id: int | None, preferred_name: str) -> int | None: ...
    """ID → name search → None (use default)."""
```

### hotkey.py

```python
class HotkeyMode(Enum):
    HOLD = "hold"; TOGGLE = "toggle"

class HotkeyListener:
    def __init__(self, hotkey: Hotkey, mode: HotkeyMode, bridge: SignalBridge): ...
    def start(self) -> None: ...
        """Install WH_KEYBOARD_LL + WH_MOUSE_LL global hooks + GetMessage pump in a daemon thread.
        Matches modifiers + trigger, swallows the matched trigger event (returns 1 from the hook).
        Emits bridge.hotkey_pressed / bridge.hotkey_released; SetWindowsHookEx failure →
        bridge.error_occurred(AppError.HOTKEY_HOOK_FAILED)."""
    def stop(self) -> None: ...
        """PostThreadMessage WM_QUIT, join thread, unhook both hooks."""
    def set_mode(self, mode: HotkeyMode) -> None: ...
    # Pure, OS-independent matching core (unit-tested without Win32):
    #   _on_kb_event(wparam, vk) -> bool ; _on_mouse_event(wparam, mouse_data) -> bool
```

### stt.py

```python
def transcribe(audio_wav: bytes, config: AppConfig) -> PipelineResult: ...
    """POST WAV to STT endpoint with verbose_json. Primary → fallback if enabled and primary fails.
    Filter: keep if ANY segment no_speech_prob < 0.7. All-above → ScreamerError(AppError.NO_SPEECH).
    HTTP/network errors → ScreamerError(AppError.STT_FAILED).
    Fallback success → PipelineResult with AppError.STT_FALLBACK_USED in warnings."""
```

### rewrite.py

```python
def rewrite(text: str, config: AppConfig) -> PipelineResult: ...
    """Send text to LLM with system prompt. Primary → fallback. Error → ScreamerError(AppError.LLM_FAILED).
    Returns input text unchanged in PipelineResult.text if config.llm_enabled is False."""
```

### injector.py

```python
def type_text(text: str, post_key: str | None = None) -> None: ...
    """Win32 SendInput (KEYEVENTF_UNICODE). 0.05s delay then press post_key if not None.
    Raises ScreamerError(AppError.INJECTION_FAILED) on failure."""
```

### icons.py

```python
class TrayState(Enum):
    IDLE = "idle"; RECORDING = "recording"; PROCESSING = "processing"

def get_icon_pixmap(state: TrayState) -> QPixmap: ...
    """32x32 QPixmap from embedded base64 PNG. Grey=idle, red=recording, yellow=processing."""

def get_icon_bytes(state: TrayState) -> bytes: ...
    """Raw PNG bytes for testing without Qt."""
```

### snackbar.py

```python
def snackbar_content_for(state_value: str) -> tuple[str, tuple[int, int, int]] | None: ...
    """Map a TrayState value to (label, dot_rgb); None for idle/unknown (hidden)."""

def bottom_center_xy(available: QRect, size: QSize, margin: int = 48) -> QPoint: ...
    """Top-left point centering *size* horizontally in *available*, *margin* above bottom."""

class RecordingSnackbar(QWidget):
    """Frameless, always-on-top, translucent, click-through status pill at the
    bottom-center of the primary screen. Pulsing dot + label.
    Never takes focus or appears in the taskbar."""
    def show_state(self, label: str, dot_rgb: tuple[int, int, int]) -> None: ...
    def hide_state(self) -> None: ...
```

### settings_dialog.py

```python
class PasswordField(QLineEdit):
    """Masked line edit with an explicit trailing show/hide toggle action."""

class SettingsDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        devices: list[tuple[int, str]] | None = None,
        calibrate_fn: Callable[[int | None], float] | None = None,
    ): ...
        """4-tab dialog (General, STT, LLM, Audio) prefilled from config.
        Edits a copy; original untouched until accept.
        *devices*: list of (device_id, display_name) for the Audio tab.
        *calibrate_fn*: fn(device_id) -> float for RMS calibration."""
    def get_config(self) -> AppConfig: ...
        """Return edited config. Call after exec() returns Accepted."""

# if __name__ == "__main__": launches standalone for testing
```

### startup.py

```python
def is_supported() -> bool: ...
    """True on Windows."""

def startup_command() -> str: ...
    """Return the command stored in HKCU Run."""

def set_enabled(enabled: bool) -> None: ...
    """Add or remove HKCU Run key. Raises ScreamerError on failure."""

def is_enabled() -> bool: ...
    """Check if startup registration is currently active."""

def sync_enabled(enabled: bool) -> None: ...
    """Idempotent: only writes registry if state differs from desired."""
```

### main.py

No public exports. Entry point only:

```python
# if __name__ == "__main__": main()
# Accepts --startup flag for silent tray launch (no auto-open settings)
```

---

## Dependency Rules

| Rule | Detail |
|------|--------|
| Composition root | `main.py` imports all other modules. Nothing imports `main.py`. |
| Settings dialog | `settings_dialog.py` imports only `config.py` and `startup.py` (and `utils.py` for constants). |
| Shared utilities | `audio.py`, `hotkey.py`, `stt.py`, `rewrite.py`, `injector.py`, `startup.py` may import `utils.py`. |
| Zero peer imports | The six backend modules must NOT import each other. |
| Config consumer | `stt.py` and `rewrite.py` receive `AppConfig` as a parameter — they do not import `config.py`. `audio.py` receives device ID, device name, and RMS threshold from `main.py`. `main.py` passes config values to all backends. |
| Qt in backend | Only `utils.py`, `icons.py`, `settings_dialog.py`, `main.py` import PySide6. Backend modules (`audio`, `hotkey`, `stt`, `rewrite`, `injector`, `startup`) do not. |
| No circular imports | The graph is a DAG rooted at `main.py`. Structural guarantee. |

---

## Platform Expectations

Windows-first project. Agents may run on Linux/macOS.

| Requirement | Detail |
|-------------|--------|
| Import safety | Every module must import on any OS. No crash at import time. |
| Windows-only runtime | `hotkey.py`, `injector.py`, and DPAPI in `config.py` must guard Win32 calls behind `platform.system() == "Windows"`. On non-Windows, raise `ScreamerError(AppError.UNSUPPORTED_PLATFORM)` (never crash at import time). |
| Non-Windows fallback | `audio.py`, `stt.py`, `rewrite.py`, `icons.py`, `config.py` (QSettings paths), `utils.py`, `settings_dialog.py` should work cross-platform where deps are installed. |
| Full verification | DPAPI roundtrip, `RegisterHotKey`, and `SendInput` can only be fully verified on Windows. |

---

## Configuration for CLI Tests

Phase 1 modules have standalone `__main__` blocks for smoke testing. STT/LLM defaults are empty. CLI scripts must resolve credentials as follows:

1. `load_config()` → read QSettings + DPAPI.
2. If `.env` exists at cwd, `import_from_env(config)` backfills empty fields.
3. If required API fields are still empty, print to stderr and `exit(1)`:

   ```
   No API configuration found. Set up credentials via:
     - Place a .env file in the project root
     - Or run python -m src.settings_dialog (Phase 2)
   ```

No hardcoded provider defaults. No silent fallback to unconfigured endpoints.

---

## Phase 1 — Backend Pipeline

**Goal:** All backend and support modules built, importable, standalone CLI smoke tests passing.

| # | File | Verification |
|---|------|-------------|
| 1 | `requirements.txt` | `pip install -r requirements.txt` succeeds |
| 2 | `src/__init__.py` | Empty; enables package imports |
| 3 | `src/utils.py` | `python -c "from src.utils import SignalBridge, AppError"` |
| 4 | `src/icons.py` | `python -m src.icons` writes 3 test PNGs (32x32) |
| 5 | `src/config.py` | `python -m src.config` prints defaults, DPAPI roundtrip, creates APP_DIR |
| 6 | `src/audio.py` | `python -m src.audio` records 3s → `test.wav`, prints duration+RMS |
| 7 | `src/hotkey.py` | `python -m src.hotkey` prints "pressed"/"released" (Windows), graceful message otherwise |
| 8 | `src/stt.py` | `python -m src.stt test.wav` prints transcription (needs API config) |
| 9 | `src/rewrite.py` | `python -m src.rewrite "test sentense wit erors"` prints corrected text (needs API config) |
| 10 | `src/injector.py` | `python -m src.injector "hello world"` types into active window (Windows), message otherwise |
| 11 | `src/startup.py` | `python -m src.startup` checks/sets Windows startup registry key |

**Verification commands:**
```bash
pip install -r requirements.txt
python -m compileall src/           # must pass on all platforms
python -c "import src; print('OK')"
```

---

## Phase 1 Acceptance Gates

- [ ] `pip install -r requirements.txt` completes without errors.
- [ ] `python -m compileall src/` passes with zero failures.
- [ ] Every Phase 1 module imports on the current OS without crashing.
- [ ] Windows-only functions raise clear `ScreamerError(AppError.X)` on non-Windows (never crash at import time).
- [ ] Standalone CLI tests pass where OS/API keys allow; graceful exit with setup message otherwise.
- [ ] No `api_key` values appear in log output.
- [ ] Transcript text appears in logs only when `debug=True`.
- [ ] Public exports match the API Contracts section above.
- [ ] `audio`, `hotkey`, `stt`, `rewrite`, `injector`, `startup` do not import each other.

---

## Review Checkpoint — STOP HERE

**After Phase 1 completes, the agent MUST stop and request review before starting Phase 2.**

The reviewer should inspect:

| Check | What to verify |
|-------|---------------|
| API shape | Exports match contracts. Can Phase 2 wire everything with only these imports? |
| Line budget | Each module within ~30% of PLAN.md Section 10 targets. |
| Error handling | Backend modules raise `ScreamerError(AppError.X)`; no bare `print()` or swallowed exceptions. |
| Logging | Secrets excluded from logs. Transcripts only logged with `debug=True`. |
| Platform guards | Windows-only modules raise clean errors on Linux/macOS at runtime, not import time. |
| Phase 2 readiness | Can `main.py` + `settings_dialog.py` be built **without modifying any Phase 1 file**? If not, fix Phase 1 now. |
| Windows-only guard | `startup.py` raises `ScreamerError(UNSUPPORTED_PLATFORM)` on non-Windows at runtime, not import time. |

Do not proceed to Phase 2 until review passes.

---

## Phase 2 — UI Shell

**Goal:** System tray application + settings dialog wrapping Phase 1 modules.

**Prerequisite:** Phase 1 reviewed and approved.

| # | File | Verification |
|---|------|-------------|
| 11 | `src/settings_dialog.py` | `python -m src.settings_dialog` launches standalone 4-tab dialog; fields persist across reopen |
| 12 | `src/main.py` | `python -m src.main` starts tray app; full dictation loop works |

**Key behaviors to test:**
- Settings survive dialog close/reopen and full app restart.
- Tray menu quick-toggles sync bidirectionally with Settings dialog values.
- Tray icon: grey (idle) → red (recording) → yellow (processing) → grey.
- Errors appear as tray balloons (user-facing `AppError` messages).
- Exit triggers graceful shutdown: cancel worker (Event, 5s timeout), stop audio, save settings, quit Qt.
- During active processing, Exit aborts within 5 seconds.

---

## Phase 2 Acceptance Gates

- [ ] `python -m src.settings_dialog` launches standalone; all tabs render.
- [ ] Settings persist across dialog reopen and full app restart.
- [ ] Tray app starts and exits cleanly (no zombie threads).
- [ ] Tray menu and Settings dialog values stay in sync bidirectionally.
- [ ] Full dictation loop works on Windows: hotkey → speak → processing → text appears.
- [ ] Worker shutdown is graceful (cancellation Event, 5s timeout, audio stream stopped).
- [ ] User-facing errors appear through tray balloons.
- [ ] Phase 2 does not modify Phase 1 APIs except for reviewed bug fixes. Any API change to Phase 1 during Phase 2 must be documented and re-reviewed.

---

## Boundaries

- No modules beyond the 11 listed. No new dependencies.
- Do not implement packaging (PyInstaller), code signing, or cross-platform hotkey backends.
- Autostart registration is implemented in `startup.py`.
- All paths: `%LOCALAPPDATA%/Screamer/`. API keys + custom headers: DPAPI. Plain settings: QSettings (IniFormat).
- If a Phase 2 bug forces a Phase 1 API change, document it in the review checkpoint and get re-approval.
