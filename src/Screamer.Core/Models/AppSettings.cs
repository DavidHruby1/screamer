using Screamer.Core.Enums;

namespace Screamer.Core.Models;

public sealed class AppSettings
{
    public HotkeyBinding Hotkey { get; init; } = new();

    public TranscriptionMode TranscriptionMode { get; init; } = TranscriptionMode.Local;

    public InjectionTarget InjectionTarget { get; init; } = InjectionTarget.Clipboard;

    public string? LocalModelPath { get; init; }

    public string? GroqApiKey { get; init; }
}
