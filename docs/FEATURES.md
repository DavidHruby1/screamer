# Future Features

## Product Scope and Priorities

Screamer is for dictating AI prompts and other short text into Windows applications.
It is not a general-purpose AI agent or an editor for existing documents.
Keep the normal workflow fast: hotkey, speak, release, receive text. Recovery
must be available on demand, not mandatory dialogs after every dictation.

This is the agreed implementation backlog, not a description of shipped features.
Priority order is intentional. Deferred and excluded items are not implementation tasks.
Current API and persistence contracts remain in [IMPLEMENTATION.md](IMPLEMENTATION.md).

| Priority | Work | Scope |
| --- | --- | --- |
| P0 | 1. Quick language switching in the tray | First delivery; do not wait for a profile system. |
| P1 | 2. Reliable insertion and clipboard output | Diagnose Notepad, handle focus changes, expose delivery failures. |
| P1 | 3. Recoverable dictation and local history | Preserve usable text and allow recovery without repeating speech. |
| P1 | 5. Microphone feedback and recovery | Show actual input and handle device lifecycle failures. |
| P2 | 6. Custom vocabulary | Improve names, technical terms, and mixed-language dictation. |
| P2 | 7. Simple rewrite profiles and app matching | Unify modes and app-specific prompts without expanding into an agent. |
| P3 | 8. Local STT server support | Integrate existing compatible servers before bundling models. |

## 1. Quick Language Switching in the Tray (P0)

Switching languages is the top user priority, not optional polish.

### Required behavior

- Let users manage a list of frequently used STT languages in Settings, including Czech and English.
- Add a tray language submenu with readable language names and a visible current selection. Keep automatic detection available as an explicit choice.
- Selecting a language in the tray persists it and applies to the next dictation without opening Settings or restarting Screamer.
- Preserve the existing configured language when introducing the list. A selection change must not alter a recording or request already in progress.
- Use the selected language consistently for primary and fallback STT and the existing rewrite language hint. A language hint must not implicitly request translation.

### Acceptance criteria

- Switch between Czech, English, and automatic detection from the tray; the next STT request uses the selected language, or omits the hint for automatic detection.
- The saved list and active choice survive restart and agree between Settings and the tray.
- Switching while a dictation is in progress affects only the next dictation.
- This feature ships independently of profiles. Switching languages is not a promise of accurate code-switching within one utterance.

## 2. Reliable Insertion and Clipboard Output (P1)

### Investigate the Notepad report

Reported symptom: dictated text appears in browsers but not in Windows Notepad,
while Screamer reports no error. Keep this as a high-priority bug to reproduce on Windows.

The previous diagnosis is not established. Current `src/hotkey.py` has no
`_watch_release`; it uses low-level hook events. Toggle mode can finish recording
while the shortcut is still held, but this alone does not prove modifier interference
causes the Notepad failure. `ctypes` structures are already zero-initialized, so
explicit zero assignments are not a supported fix by themselves. Do not assume all
Notepad versions and browser text fields handle injected input in the same way.

Reproduce in hold and toggle modes with different modifier-release timing. Record
the Windows/Notepad versions, target application, focus, and `SendInput` result.
Keep diagnostic logging free of transcript text, secrets, and unnecessary window titles.
Choose the fix from that evidence rather than the obsolete line references.

### Required behavior

- Support three output modes: type into the target application, copy to clipboard, and copy plus type. Keep direct typing as the default; copy plus type is explicit opt-in.
- Capture the intended foreground target when recording starts and check it before automatic insertion. If the target window changed or disappeared, retain the text and offer recovery instead of typing into the new window or forcibly restoring focus.
- Distinguish transcription completion, an attempted insertion, and a reported insertion failure. A successful `SendInput` return only confirms submitted input events, not visible text in the intended field.
- On failure or uncertain/partial insertion, preserve the result and offer copy or explicit retry after the user chooses a destination. Never automatically resend the whole text: a prefix may already have been inserted.
- Apply the post-type key only to a completed typing attempt, not clipboard-only output, withheld insertion, or a reported partial failure. Cancelling or disabling dictation must still prevent automatic insertion.

### Acceptance criteria

- Verify the reproduced Notepad case and browser/editor insertion on Windows in hold and toggle modes, including Czech characters, punctuation, and line breaks.
- Changing the foreground target during processing does not cause automatic insertion or a post-type key in the new target; the result remains recoverable.
- Copy-only leaves the target untouched. Copy plus type uses the selected result and is never enabled implicitly.
- A reported insertion failure exposes usable text and does not trigger an automatic duplicate attempt. Manual recovery must not type into Screamer's own UI.
- Keep the limitation explicit: checking the target window does not prove that the same text field or caret position is still active. Clipboard output is an alternative, not a universal delivery guarantee.

## 3. Recoverable Dictation and Local History (P1)

Do not make the user repeat speech because a later pipeline step failed. This extends
history beyond a list of successful final outputs and supports the recovery actions in section 2.

### Required behavior

- Preserve the raw STT transcript before rewriting and retain the rewritten result separately. A rewrite or insertion failure must not discard an already available transcript.
- Provide on-demand actions to copy or insert either version and rerun rewriting from the raw transcript. Rerunning cleanup must not require STT or a new recording, overwrite the raw transcript, or insert automatically.
- Keep bounded local text history with timestamps, available raw/rewritten text, language, target application when known, pipeline warnings, and the last delivery attempt's outcome. Do not label submitted input as verified delivery.
- Provide individual deletion, clear-all, configurable retention, and an option to disable persistent history. Even with history disabled, keep the current result available for immediate recovery without writing it to disk.
- Keep audio off disk in this scope. After an STT failure, retain at most one pending recording in memory for explicit retry. Release that audio after successful transcription, explicit discard, replacement by a new recording, or application exit; make that temporary lifetime clear.

### Acceptance criteria

- If rewriting fails or returns empty output, preserve the existing raw-text fallback and warning; the raw result is also available for recovery.
- If insertion fails or is withheld, the user can retrieve the text without dictating again.
- Retrying a failed STT request uses the retained recording; cancelling or disabling does not silently retry or deliver anything.
- Rewriting a saved raw transcript preserves the original and makes the new result available for inspection and explicit output.
- Retention and deletion remove the relevant stored text. Persistent history contains no audio. In-memory audio recovery does not claim to survive an application crash or restart.

## 5. Microphone Feedback and Recovery (P1)

The current recording indicator and RMS calibration are useful, but a recording
state alone does not show that audio is actually arriving.

### Required behavior

- Show a live input-level indicator based on captured audio and make the active microphone identifiable without leaving the dictation workflow.
- Distinguish an unavailable device, a capture failure, and a valid but quiet recording. A quiet signal is not automatically a hardware failure; a nonzero signal is not proof of intelligible speech.
- Handle unplug/replug, default-device changes, and sleep/resume so the next recording either uses the configured device policy or shows a clear actionable error.
- Offer retry/reselection when capture cannot proceed. Do not silently switch away from an explicitly selected microphone or require an application restart for a recoverable device problem.

### Acceptance criteria

- The meter reacts to actual input, not only a timer or the recording state; unavailable input is not presented as healthy capture.
- Verify explicit-device and system-default selection through device changes and Windows sleep/resume.
- A capture failure exposes the relevant microphone/error and a recovery action. Existing silence calibration still works, and ordinary pauses do not create repeated warnings.

## 6. Custom Vocabulary / Personal Dictionary (P2)

Let users define names, acronyms, project terms, product names, usernames, and
domain-specific jargon with their preferred spellings.

### Required behavior and acceptance

- Allow adding, editing, and removing vocabulary entries and persist them locally.
- Use vocabulary as context for rewriting and as STT prompting only when the configured provider supports it. Unsupported STT prompting must not break transcription.
- Keep preferred-spelling guidance distinct from a guarantee of exact replacement. Do not repair technical terms by inventing content or translating identifiers.
- Verify useful examples of Czech sentences containing English technical terms, names, numbers, and identifiers. Evaluate STT errors separately from errors introduced by cleanup.

Mixed-language quality is a validation goal, not a new automatic language-routing
system or a claim that a vocabulary prompt can correct every recognition error.

## 7. Simple Rewrite Profiles and App Matching (P2)

Unify the previous "App-Specific Rewrite Prompts" and "Rewrite Modes / Styles"
into one small profile feature rather than two overlapping configuration systems.

### Required behavior

- Start with raw transcription, clean dictation, and user-defined rewrite profiles. Reuse the existing editable system prompt.
- Allow manual profile selection and optional foreground-application matching. Match the application captured at recording start, not whichever app becomes active during processing.
- Keep the choice visible. An explicit manual override takes precedence over automatic app matching; with neither, use the user's default profile. Freeze the effective choice for each dictation.
- Keep language switching independently accessible in the tray. Do not make users build a profile just to change STT language.
- Do not read screen contents, selected text, or clipboard context to choose a profile. One browser process can host several different tasks; manual selection must remain available.

### Acceptance criteria

- Raw mode performs no LLM call; clean mode uses the existing conservative cleanup default documented in [IMPLEMENTATION.md](IMPLEMENTATION.md#configpy).
- Saved custom prompts remain editable and work with manual selection and optional app matching.
- Application changes during processing do not change the selected prompt or bypass output target checks.
- Email, casual text, bullets, coding prompts, and translation may be custom profile uses, not a mandatory catalog of separate features. All operate on newly dictated text, not existing documents or external actions.

## 8. Local STT Server Support (P3)

Support existing local OpenAI-compatible transcription servers before considering
bundled models. Candidate integrations include `faster-whisper-server`, compatible
`whisper.cpp` server configurations, and other compatible local endpoints.

### Required behavior and acceptance

- Provide working configuration examples or minimal presets and setup instructions. Verify the actual server API instead of assuming every local engine exposes the same endpoint.
- Support local servers that do not require an API key; the current provider validation and STT request path expect one, so documentation alone is insufficient.
- Keep manual endpoint/model configuration and existing authenticated providers working. Do not introduce a provider-catalog maintenance or model-discovery project.
- Demonstrate dictation with networking unavailable using a running local STT server, rewriting disabled, and no cloud fallback. Document that local STT alone does not make cloud rewriting or fallback local.

An enforced local-only/privacy policy is deferred. Bundling models, GPU/runtime
management, and a one-click offline installation are not part of this first integration.

## Deferred and Excluded Work

### Deferred

- Snippets / voice shortcuts: saved expansions such as links or signoffs may be useful later. Do not implement them before the active priorities; accidental activation within normal dictation needs a deliberate design.
- Usage dashboard: remove word counts, top-app charts, and estimated time saved from the active roadmap. Basic failure/latency diagnostics may support the work above, but are not a new analytics feature.
- Enforced local-only/privacy mode: not wanted now. Do not add endpoint restrictions, data-flow policy controls, or cloud-fallback consent workflows as part of the accepted work.

### Outside Product Scope

- Voice editing or replacement of selected existing text; general AI assistants, agents, and execution of spoken instructions.
- Automatic screen/clipboard context collection, document editing, and universal undo in external applications.
- Meetings, speaker diarization, team collaboration, mobile applications, and a general file-transcription workbench.
- Realtime streaming and queues of concurrent dictations without a separately demonstrated need.
- Provider model-catalog upkeep and discovery. Existing manual provider configuration is sufficient for this roadmap.

## Supporting Failure Reports

These reports motivate the accepted failure scenarios, not new features or claims
that competitors still have every reported defect. Issue closure alone is not proof
of a fix, and reports on another OS are not reproductions of Screamer bugs.

- Lost recordings: [Handy #783](https://github.com/cjpais/Handy/issues/783).
- Incorrect clipboard delivery: [Handy #502](https://github.com/cjpais/Handy/issues/502).
- Rewrite content substitution: [OpenWhispr #2225](https://github.com/OpenWhispr/openwhispr/issues/2225) and the narrower scope of [PR #2300](https://github.com/OpenWhispr/openwhispr/pull/2300).
- Empty capture after a device-format change: [VoiceInk #956](https://github.com/Beingpax/VoiceInk/issues/956).
