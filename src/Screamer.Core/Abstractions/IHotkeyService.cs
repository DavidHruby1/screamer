namespace Screamer.Core.Abstractions;

public interface IHotkeyService
{
    event EventHandler? DictationStarted;
    event EventHandler? DictationStopped;

    Task StartAsync(CancellationToken cancellationToken);

    Task StopAsync(CancellationToken cancellationToken);
}
