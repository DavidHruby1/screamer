using Screamer.Core.Abstractions;

namespace Screamer.Infrastructure.Injection;

public sealed class FocusedWindowTextInjector : ITextInjector
{
    public Task InjectAsync(string text, CancellationToken cancellationToken)
    {
        return Task.CompletedTask;
    }
}
