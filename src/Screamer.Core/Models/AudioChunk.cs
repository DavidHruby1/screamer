namespace Screamer.Core.Models;

public sealed class AudioChunk
{
    public required byte[] Buffer { get; init; }

    public int BytesRecorded { get; init; }

    public bool IsFinal { get; init; }
}
