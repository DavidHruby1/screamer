namespace Screamer.Core.Abstractions;

public interface IHotkeyService
{
    event EventHandler? DictationStarted;

    Task StartAsync(CancellationToken cancellationToken);

    Task StopAsync(CancellationToken cancellationToken);
}
