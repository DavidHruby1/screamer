using Screamer.Core.Abstractions;
using Screamer.Core.Models;

namespace Screamer.Transcription.Groq;

public sealed class GroqTranscriptionProvider : ITranscriptionProvider
{
    public Task<TranscriptResult> TranscribeOnceAsync(
        AudioCaptureResult audio,
        CancellationToken cancellationToken)
    {
        return Task.FromResult(new TranscriptResult());
    }
}
