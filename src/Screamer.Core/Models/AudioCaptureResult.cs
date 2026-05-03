namespace Screamer.Core.Models;

public sealed class AudioCaptureResult
{
    public required byte[] AudioBytes { get; init; }

    public required string ContentType { get; init; }

    public TimeSpan Duration { get; init; }
}
