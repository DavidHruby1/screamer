# Future Features

## PRIORITY FEATURE**

Fix this issue:
When dictating with Screamer, injected text appears correctly in web browsers but NOT in Windows Notepad. The app reports success - no error is shown.
Root Cause: Modifier Key Interference
The default hotkey is Ctrl+Alt+Space. In src/hotkey.py:215-229, the release watcher (_watch_release) only polls for the primary key (Space, VK=0x20). It does not monitor the modifier keys (Ctrl, Alt):
state = user32.GetAsyncKeyState(vk)  # only checks 0x20 (Space)
In toggle mode, _finalize_recording() is called directly from the hotkey-pressed handler while Ctrl+Alt+Space is still physically held. The type_text() call via SendInput with KEYEVENTF_UNICODE then runs while Ctrl/Alt are still logically down.
Notepad uses the classic Win32 EDIT control, which respects the current keyboard modifier state. When Ctrl or Alt is held, characters injected via KEYEVENTF_UNICODE/VK_PACKET can be dropped or misinterpreted as accelerators.
Browsers work because they use modern text frameworks (TSF, DirectInput, contenteditable) that handle VK_PACKET robustly regardless of modifier state - so the same SendInput call succeeds there.
Possible Fixes
Fix	What	Where
1. Wait for all hotkey keys	Poll GetAsyncKeyState for both the primary key AND modifier keys (Ctrl, Alt) before emitting hotkey_released	src/hotkey.py:_watch_release
2. Explicitly init KEYBDINPUT	Set wVk = 0, time = 0, dwExtraInfo = 0 explicitly in _send_unicode and _send_vk (currently relies on implicit ctypes zero-init)	src/injector.py:_send_unicode, _send_vk
3. Log target window	Call GetForegroundWindow + GetClassNameW before SendInput to see which window actually receives the text	src/injector.py:type_text
Fix #1 is the most important - it prevents the pipeline from injecting text while modifier keys are still physically held. Fixes #2 and #3 are low-risk hygiene improvements that help with debugging.

---

## 1. Custom Vocabulary / Personal Dictionary

Let users define names, acronyms, project terms, product names, usernames, and domain-specific jargon.

Use this vocabulary in the LLM rewrite prompt, and in STT prompts if the configured provider supports prompting.

## 2. App-Specific Rewrite Prompts

Detect the active foreground app or window and apply a matching rewrite prompt.

Examples:

- Slack: casual and concise.
- Email: polished and professional.
- Cursor/OpenCode: precise technical prompt.
- Notes: clean bullets or paragraphs.

## 3. Rewrite Modes / Styles

Add selectable rewrite modes for common output shapes.

Examples:

- Raw transcription.
- Clean dictation.
- Professional email.
- Casual message.
- Bullet notes.
- Coding prompt.
- Translate to English.

## 4. Snippets / Voice Shortcuts

Allow spoken cues to expand into saved text snippets.

Examples:

- "insert calendar link" -> saved scheduling link.
- "support signoff" -> saved support closing text.
- "meeting intro" -> reusable meeting template.

## 5. Clipboard Mode and Output Controls

Support output modes beyond direct typing.

Modes:

- Type into active app.
- Copy to clipboard.
- Copy and type.

This helps when focus changes or when an app does not handle `SendInput` well.

## 6. Transcription History

Keep a local history of recent transcriptions.

Store timestamp, final text, target app/window if available, and pipeline warnings.

Audio should stay off by default unless explicitly added later.

## 7. Local Offline STT Backend

Support local/private transcription through local OpenAI-compatible backends.

Possible integrations:

- `faster-whisper-server`
- `whisper.cpp`
- Other local Whisper-compatible servers

First version can be provider presets/documentation rather than bundling models.

## 8. Usage Dashboard

Low-priority polish feature.

Show simple usage stats such as words dictated, estimated time saved, and top target apps.

## 9. Language List

Add a possibility to create list of languages the user wants to use frequently.

Then add the option to quickly switch between them in the Tray.
