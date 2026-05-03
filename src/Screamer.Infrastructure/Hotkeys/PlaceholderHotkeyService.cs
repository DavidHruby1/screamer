using Screamer.Core.Abstractions;

namespace Screamer.Infrastructure.Hotkeys;

public sealed class PlaceholderHotkeyService : IHotkeyService
{
    public event EventHandler? DictationStarted;

    public event EventHandler? DictationStopped;

    public Task StartAsync(CancellationToken cancellationToken)
    {
        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken)
    {
        return Task.CompletedTask;
    }

    public void RaiseStart() => DictationStarted?.Invoke(this, EventArgs.Empty);

    public void RaiseStop() => DictationStopped?.Invoke(this, EventArgs.Empty);
}
