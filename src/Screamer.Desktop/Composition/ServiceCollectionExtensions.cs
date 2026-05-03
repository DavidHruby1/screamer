using Microsoft.Extensions.DependencyInjection;

namespace Screamer.Desktop.Composition;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddScreamerDesktop(this IServiceCollection services)
    {
        return services;
    }
}
