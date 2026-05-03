namespace Screamer.Core.Abstractions;

public interface ITextInjector
{
    Task InjectAsync(string text, CancellationToken cancellationToken);
}
