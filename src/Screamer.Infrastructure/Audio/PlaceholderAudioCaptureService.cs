using Screamer.Core.Abstractions;
using Screamer.Core.Models;

namespace Screamer.Infrastructure.Audio;

public sealed class PlaceholderAudioCaptureService : IAudioCaptureService
{
    public Task<AudioCaptureResult> CaptureOnceAsync(CancellationToken cancellationToken)
    {
        var result = new AudioCaptureResult
        {
            AudioBytes = [],
            ContentType = "audio/wav",
            Duration = TimeSpan.Zero
        };

        return Task.FromResult(result);
    }
}
