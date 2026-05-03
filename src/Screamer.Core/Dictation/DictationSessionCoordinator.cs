using Screamer.Core.Abstractions;
using Screamer.Core.Enums;
using Screamer.Core.Models;

namespace Screamer.Core.Dictation;

public sealed class DictationSessionCoordinator(
    IAudioCaptureService audioCaptureService,
    ITranscriptionProvider transcriptionProvider,
    ITextInjector textInjector)
{
    public DictationState State { get; private set; } = DictationState.Idle;

    public async Task<TranscriptResult> RunOnceAsync(CancellationToken cancellationToken)
    {
        State = DictationState.Capturing;
        var audio = await audioCaptureService.CaptureOnceAsync(cancellationToken);

        State = DictationState.Transcribing;
        var transcript = await transcriptionProvider.TranscribeOnceAsync(audio, cancellationToken);

        State = DictationState.Injecting;
        await textInjector.InjectAsync(transcript.Text, cancellationToken);

        State = DictationState.Idle;
        return transcript;
    }
}
