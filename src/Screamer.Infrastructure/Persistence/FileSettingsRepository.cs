using Screamer.Core.Abstractions;
using Screamer.Core.Models;

namespace Screamer.Infrastructure.Persistence;

public sealed class FileSettingsRepository : ISettingsRepository
{
    public Task<AppSettings> LoadAsync(CancellationToken cancellationToken)
    {
        return Task.FromResult(new AppSettings());
    }

    public Task SaveAsync(AppSettings settings, CancellationToken cancellationToken)
    {
        return Task.CompletedTask;
    }
}
