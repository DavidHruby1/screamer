using Screamer.Core.Models;

namespace Screamer.Core.Abstractions;

public interface IAudioCaptureService
{
    Task<AudioCaptureResult> CaptureOnceAsync(CancellationToken cancellationToken);
}
