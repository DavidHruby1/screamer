using System.Threading;
using Screamer.Core.Abstractions;

namespace Screamer.Infrastructure.Injection;

public sealed class ClipboardTextInjector : ITextInjector
{
    public Task InjectAsync(string text, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();

        if (Thread.CurrentThread.GetApartmentState() != ApartmentState.STA)
            throw new InvalidOperationException(
                "Clipboard injection requires an STA thread.");

        Clipboard.SetText(text);
        return Task.CompletedTask;
    }
}
