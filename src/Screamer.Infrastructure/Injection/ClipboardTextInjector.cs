using Screamer.Core.Abstractions;

namespace Screamer.Infrastructure.Injection;

public sealed class ClipboardTextInjector : ITextInjector
{
    public Task InjectAsync(string text, CancellationToken cancellationToken)
    {
        return Task.CompletedTask;
    }
}
