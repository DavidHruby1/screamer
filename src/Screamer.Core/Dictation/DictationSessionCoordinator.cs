using Screamer.Core.Abstractions;
using Screamer.Core.Enums;
using Screamer.Core.Models;

namespace Screamer.Core.Dictation;

public sealed class DictationSessionCoordinator(
    IAudioCaptureService audioCaptureService,
    ITranscriptionProvider transcriptionProvider,
    ITextInjector textInjector)
{
    private readonly object _lock = new();
    private bool _isRunning;
    private DictationState _state = DictationState.Idle;

    public DictationState State
    {
        get => _state;
        private set
        {
            if (_state == value) return;
            _state = value;
            StateChanged?.Invoke(value);
        }
    }

    public event Action<DictationState>? StateChanged;

    public async Task<TranscriptResult> RunOnceAsync(CancellationToken cancellationToken)
    {
        lock (_lock)
        {
            if (_isRunning)
                throw new InvalidOperationException("A dictation session is already in progress.");
            _isRunning = true;
        }

        try
        {
            State = DictationState.Capturing;
            var audio = await audioCaptureService.CaptureOnceAsync(cancellationToken);

            cancellationToken.ThrowIfCancellationRequested();

            State = DictationState.Transcribing;
            var transcript = await transcriptionProvider.TranscribeOnceAsync(audio, cancellationToken);

            cancellationToken.ThrowIfCancellationRequested();

            State = DictationState.Injecting;
            await textInjector.InjectAsync(transcript.Text, cancellationToken);

            State = DictationState.Idle;
            return transcript;
        }
        catch (OperationCanceledException)
        {
            State = DictationState.Idle;
            throw;
        }
        catch
        {
            State = DictationState.Error;
            State = DictationState.Idle;
            throw;
        }
        finally
        {
            lock (_lock)
            {
                _isRunning = false;
            }
        }
    }
}
