using Screamer.Core.Abstractions;
using Screamer.Core.Models;

namespace Screamer.Infrastructure.Transcription;

public sealed class FakeTranscriptionProvider : ITranscriptionProvider
{
    public Task<TranscriptResult> TranscribeOnceAsync(
        AudioCaptureResult audio,
        CancellationToken cancellationToken)
    {
        var result = new TranscriptResult
        {
            Text = "hello from screamer"
        };

        return Task.FromResult(result);
    }
}
