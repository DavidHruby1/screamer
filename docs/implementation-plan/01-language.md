# 1. Quick Language Switching in the Tray

> **Status:** code implemented (2026-09-29); packaged Windows tray and long-recording
> manual acceptance still pending. See [progress](../IMPLEMENTATION-PLAN.md#progress-2026-09-29)
> and the shipped API in [IMPLEMENTATION.md](../IMPLEMENTATION.md).
> This plan expands P0 from [FEATURES.md](../FEATURES.md#1-quick-language-switching-in-the-tray-p0).

## Summary

Add an ordered, user-managed set of frequently used STT language codes while
keeping `AppConfig.stt_language` as the one active value. The tray is the fast
switching surface; Settings manages the available favorites. The critical
boundary is recording start: main takes one `deepcopy(AppConfig)` before
recording and does not mutate that session snapshot afterward. A tray selection
changes live/persisted config only, so an active session's primary STT,
fallback STT and rewrite hint remain consistent.

## Start Here

- [`src/config.py`](../../src/config.py): `AppConfig`, `load_config`,
  `save_config`, `validate_config`; owns persistence codecs and validation.
- [`src/main.py`](../../src/main.py): `_rebuild_menu`, `_add_choice_submenu`,
  tray setters and recording lifecycle; owns visible active choice and session
  timing.
- [`src/settings_dialog.py`](../../src/settings_dialog.py):
  `SettingsDialog._build_stt_tab`, `_populate`, `_collect`; owns draft editing.
- [`src/stt.py`](../../src/stt.py) and [`src/rewrite.py`](../../src/rewrite.py):
  consume `stt_language` without a signature change.

## Implemented Data Contract

`AppConfig` has `stt_language_favorites: list[str] = field(default_factory=list)`
and retains `stt_language: str = ""`, with empty meaning automatic detection.
Favorites serialize as a JSON array under `stt_language_favorites`, e.g.
`"[\"cs\",\"en\",\"de\"]"`. `config.py` owns the codec directly:

```python
def normalize_language_code(value: str) -> str: ...
def normalize_language_favorites(codes: list[str]) -> list[str]: ...
def language_choices(active: str, favorites: list[str]) -> list[tuple[str, str]]: ...
```

Canonical language identifiers are trimmed lower-case BCP-47-like tags (for
example `cs`, `en`, `pt-br`); do not claim validation against a complete
IANA/provider catalog. Settings rejects malformed syntax. The engineering
bound is 32 characters/code and 32 favorites. Stable
deduplication is case-insensitive after normalization, preserving first order.
`""` is not a favorite: Auto is always a fixed tray choice.

Known labels can be a small local mapping (`cs` -> `Czech`, `en` -> `English`);
unknown valid codes display their code. Do not add a provider registry or imply
that every configured STT provider supports every code.

## How It Works

```mermaid
flowchart LR
    UI[Settings language + favorites] --> C[config.py codec and validation]
    TR[Tray language choice] --> C
    C --> INI[Atomic settings.ini]
    C --> MAIN[main.py live AppConfig]
    MAIN --> S[Recording-start session copy]
    S --> STT[stt.py primary / fallback multipart]
    S --> RW[rewrite.py language hint]
```

### Persistence and migration

The loader explicitly decodes this JSON-backed list and distinguishes a missing
INI key from malformed contents:

| Stored state | Active language after load | Favorites after load |
| --- | --- | --- |
| Key absent (old install) | Preserve existing `stt_language` (normalize if valid) | `[]` |
| Valid JSON array | Preserve active value | Normalize and deduplicate |
| Malformed JSON, wrong root type, invalid entries | Preserve active value | Safe empty list; log a non-sensitive warning |
| Valid legacy language from INI or `.env`, not a favorite | Preserve it | Do not silently discard it from the active display |

When building tray choices, include Auto, Czech and English, all favorites,
and the current nonempty active code if otherwise absent. This prevents an old
configured value or `.env` backfill from becoming invisible. Do not infer that
an active code should be made a favorite.

`save_config` continues to write the single atomic INI replacement. Encode the
list explicitly rather than relying on QSettings' QVariant list conversion.
The favorites and active value are saved in one config write, so a tray
selection persists the same `stt_language` edited by Settings.

### Settings draft and tray

The STT tab now has an active-language combo and ordered favorites editor with
add, edit and remove. Czech and English stay available independent of the
favorites list. Unknown currently active values must remain selectable/displayed
until the user explicitly changes them. The dialog's existing deep-copy model
means Cancel discards active language and favorite edits; Apply/OK validate and
persist both together.

The tray `Language` submenu follows existing `_add_choice_submenu`: one
exclusive visible selection, Auto plus built-ins and current favorites. On
selection, update live config, save, then rebuild or reflect selection. On
save failure, restore the old live active value and keep the displayed choice
consistent with persisted state; report the save error. Keep this action
independent of future profiles.

### Session timing and requests

At the very beginning of `_start_recording`, create the one session
`copy.deepcopy(self._config)` before starting audio or allowing any subsequent
tray config mutation. The private main-owned session retains that exact config;
it is read-only after capture. Pass it through finalization to `_WorkerThread`.
Do not take a second backend snapshot or change `transcribe(audio_wav, config)`
or `rewrite(text, config)` signatures.

`stt.transcribe` already reads the same `config.stt_language` for its primary
and fallback attempts and omits `language` when empty. `rewrite.rewrite` already
appends that language as a hint. Preserve this behavior: the hint is not a
translation instruction. A language change during capture or network work
updates live config and disk for the next session only.

| Invariant / race | Owner and enforcement |
| --- | --- |
| One authoritative active language; favorites are only menu choices. | `AppConfig.stt_language` in config/Settings/tray; never derive it from list order. |
| Failed tray save does not advertise an unsaved active choice. | Main rolls back live code and visible radio selection before reporting error. |
| Session language never changes after recording starts. | One main-owned config copy; worker uses it for both STT attempts and rewrite. |
| Settings Apply cannot overwrite an intervening tray selection. | Disable config-mutating tray controls while modal Settings nested event loop runs. |
| Invalid favorites do not erase an old valid scalar language. | Config decoder defaults only the malformed collection, preserving the scalar. |

```mermaid
sequenceDiagram
    actor User
    participant Tray as main.py tray
    participant Session as main.py session
    participant Worker as _WorkerThread
    participant STT as stt.transcribe
    participant LLM as rewrite.rewrite
    User->>Tray: select language
    Tray->>Tray: update active config + atomic save
    User->>Tray: start recording
    Tray->>Session: deepcopy config once
    User->>Tray: select another language while processing
    Tray->>Tray: update live config only
    Session->>Worker: same read-only session config
    Worker->>STT: transcribe(WAV, snapshot)
    STT->>STT: primary and fallback use snapshot language
    Worker->>LLM: rewrite(raw, snapshot)
    LLM->>LLM: language hint from same snapshot
```

## Data Flow

1. Startup loads scalar `stt_language` and explicitly decodes favorites; a
   missing/malformed favorite value does not erase the active setting.
2. Settings edits a deep-copy draft; Apply/OK validates and atomically saves
   the active code and ordered favorites, while Cancel drops the draft.
3. A tray choice persists a new active code immediately; it does not mutate an
   active session's copy.
4. Recording start captures one config snapshot; STT primary/fallback and
   optional rewrite consume that same language value.

## Key Dependencies

- P0 ships independently of app profiles. Do not store language under a profile
  or make profile selection a prerequisite.
- `_add_choice_submenu` provides the tray's existing radio-choice pattern;
  `SettingsDialog`'s Apply/Cancel workflow remains authoritative for drafts.
- STT language support is provider-dependent; this feature chooses a hint and
  does not guarantee recognition quality or intra-utterance code switching.

## Known Risks

- Malformed favorites default to an empty list without changing `stt_language`;
  the loader logs a warning without language data.
- The implemented BCP-47-like syntax and bounds (32 characters/code, 32
  favorites) are engineering constraints, not provider capability checks.
- Settings runs a nested Qt event loop. Config-mutating tray controls are
  disabled while its draft is open (Enable and Exit remain usable), preventing
  an Apply from overwriting an intervening tray choice.
- Provider acceptance of codes cannot be determined from this repository;
  unknown-code display is necessary because an in-house exhaustive catalog
  would be misleading.

## Slices and Verification

1. **Implemented:** codec, `AppConfig` field and load/save/validation tests. Cover missing
   key migration, malformed JSON, wrong root type, normalization, stable
   deduplication, invalid syntax, bounds, ordering, and round trip.
2. **Implemented:** STT Settings editing and tests for initial population, Apply/OK persistence,
   invalid edit rejection and Cancel behavior, including an unknown active code.
3. **Implemented:** tray submenu and persistence handling; tests cover Auto, `cs`, `en`, extra
   favorite, unknown active selection, failed save rollback and visible choice.
4. **Implemented:** capture at recording start, freeze the
   worker and post-key settings, and test changed live config during recording
   and processing. Request tests assert primary/fallback and rewrite use a
   frozen language, and Auto omits the STT field and rewrite hint. Settings
   also disables config-mutating tray controls throughout the nested dialog.
   The next session after an actual tray selection is covered by an offscreen test.
5. **Pending:** Windows manual switch during a long recording and confirm current/next
   request behavior. Automated request tests do not establish real provider
   language quality.

## Sources

- [`docs/FEATURES.md`](../FEATURES.md#1-quick-language-switching-in-the-tray-p0):
  required behavior, acceptance and P0 independence.
- [`docs/IMPLEMENTATION-PLAN.md`](../IMPLEMENTATION-PLAN.md#1-quick-language-switching-in-the-tray-p0):
  initial design, migration intent and tests; underspecifies schema and limits.
- [`src/config.py`](../../src/config.py): active `stt_language`, JSON favorites,
  atomic INI save and `.env` backfill.
- [`src/settings_dialog.py`](../../src/settings_dialog.py): active-language selector,
  favorites editor and deep-copy/Apply/Cancel workflow.
- [`src/main.py`](../../src/main.py): tray submenu pattern, immediate setters,
  and the shipped recording-start session snapshot.
- [`src/stt.py`](../../src/stt.py), [`src/rewrite.py`](../../src/rewrite.py):
  current language propagation and automatic-language omission.
- [`tests/test_config.py`](../../tests/test_config.py),
  [`tests/test_settings_dialog.py`](../../tests/test_settings_dialog.py),
  [`tests/test_tray_menu.py`](../../tests/test_tray_menu.py),
  [`tests/test_stt_rewrite.py`](../../tests/test_stt_rewrite.py): existing
  observable test boundaries.
