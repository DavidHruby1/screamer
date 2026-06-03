# Future Features

## PRIORITY FEATURE**

Async batching of transcriptions after 5 seconds using queue to boost performance significantly.

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

## 10. Recording Snackbar

Add live recording indicator in the middle of the bottom of the screen.

It's gonna be small animated snackbar showing that the audio is being recorded.
