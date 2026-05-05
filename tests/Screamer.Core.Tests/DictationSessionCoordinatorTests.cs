using Screamer.Core.Abstractions;
using Screamer.Core.Dictation;
using Screamer.Core.Enums;
using Screamer.Core.Models;
using Xunit;

namespace Screamer.Core.Tests;

public sealed class DictationSessionCoordinatorTests
{
    [Fact]
    public async Task RunOnceAsync_CompletesFakeDictationFlow()
    {
        var states = new List<DictationState>();
        var injector = new RecordingTextInjector();
        var coordinator = new DictationSessionCoordinator(
            new SuccessfulAudioCaptureService(),
            new SuccessfulTranscriptionProvider(),
            injector);
        coordinator.StateChanged += states.Add;

        var result = await coordinator.RunOnceAsync(CancellationToken.None);

        Assert.Equal("hello from screamer", result.Text);
        Assert.Equal("hello from screamer", injector.InjectedText);
        Assert.Equal(
            [DictationState.Capturing, DictationState.Transcribing, DictationState.Injecting, DictationState.Idle],
            states);
    }

    [Fact]
    public async Task RunOnceAsync_ReturnsToIdleAfterFailure()
    {
        var states = new List<DictationState>();
        var transcriptionProvider = new FailingThenSuccessfulTranscriptionProvider();
        var coordinator = new DictationSessionCoordinator(
            new SuccessfulAudioCaptureService(),
            transcriptionProvider,
            new RecordingTextInjector());
        coordinator.StateChanged += states.Add;

        await Assert.ThrowsAsync<InvalidOperationException>(() => coordinator.RunOnceAsync(CancellationToken.None));

        Assert.Equal(
            [DictationState.Capturing, DictationState.Transcribing, DictationState.Error, DictationState.Idle],
            states);

        var result = await coordinator.RunOnceAsync(CancellationToken.None);
        Assert.Equal("hello from screamer", result.Text);
    }

    private sealed class SuccessfulAudioCaptureService : IAudioCaptureService
    {
        public Task<AudioCaptureResult> CaptureOnceAsync(CancellationToken cancellationToken)
        {
            return Task.FromResult(new AudioCaptureResult
            {
                AudioBytes = [],
                ContentType = "audio/wav",
                Duration = TimeSpan.Zero
            });
        }
    }

    private sealed class SuccessfulTranscriptionProvider : ITranscriptionProvider
    {
        public Task<TranscriptResult> TranscribeOnceAsync(AudioCaptureResult audio, CancellationToken cancellationToken)
        {
            return Task.FromResult(new TranscriptResult { Text = "hello from screamer" });
        }
    }

    private sealed class FailingThenSuccessfulTranscriptionProvider : ITranscriptionProvider
    {
        private bool _hasFailed;

        public Task<TranscriptResult> TranscribeOnceAsync(AudioCaptureResult audio, CancellationToken cancellationToken)
        {
            if (!_hasFailed)
            {
                _hasFailed = true;
                throw new InvalidOperationException("transcription failed");
            }

            return Task.FromResult(new TranscriptResult { Text = "hello from screamer" });
        }
    }

    private sealed class RecordingTextInjector : ITextInjector
    {
        public string? InjectedText { get; private set; }

        public Task InjectAsync(string text, CancellationToken cancellationToken)
        {
            InjectedText = text;
            return Task.CompletedTask;
        }
    }
}
