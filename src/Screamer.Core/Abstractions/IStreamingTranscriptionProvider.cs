using Screamer.Core.Models;

namespace Screamer.Core.Abstractions;

public interface IStreamingTranscriptionProvider
{
    IAsyncEnumerable<TranscriptSegment> TranscribeAsync(
        IAsyncEnumerable<AudioChunk> audio,
        CancellationToken cancellationToken);
}
