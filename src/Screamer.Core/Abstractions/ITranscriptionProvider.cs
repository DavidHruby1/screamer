using Screamer.Core.Models;

namespace Screamer.Core.Abstractions;

public interface ITranscriptionProvider
{
    Task<TranscriptResult> TranscribeOnceAsync(
        AudioCaptureResult audio,
        CancellationToken cancellationToken);
}
