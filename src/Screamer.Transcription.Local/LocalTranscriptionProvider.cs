using Screamer.Core.Abstractions;
using Screamer.Core.Models;

namespace Screamer.Transcription.Local;

public sealed class LocalTranscriptionProvider : ITranscriptionProvider
{
    public Task<TranscriptResult> TranscribeOnceAsync(
        AudioCaptureResult audio,
        CancellationToken cancellationToken)
    {
        return Task.FromResult(new TranscriptResult());
    }
}
