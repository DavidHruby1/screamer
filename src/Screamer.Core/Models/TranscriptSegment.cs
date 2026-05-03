namespace Screamer.Core.Models;

public sealed class TranscriptSegment
{
    public string Text { get; init; } = string.Empty;

    public bool IsFinal { get; init; }
}
