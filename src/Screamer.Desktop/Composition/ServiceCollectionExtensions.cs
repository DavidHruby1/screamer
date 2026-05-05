using Microsoft.Extensions.DependencyInjection;
using Screamer.Core.Abstractions;
using Screamer.Core.Dictation;
using Screamer.Desktop.ViewModels;
using Screamer.Infrastructure.Audio;
using Screamer.Infrastructure.Hotkeys;
using Screamer.Infrastructure.Injection;
using Screamer.Infrastructure.Persistence;
using Screamer.Infrastructure.Transcription;

namespace Screamer.Desktop.Composition;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddScreamerDesktop(this IServiceCollection services)
    {
        services.AddSingleton<MainWindow>();
        services.AddTransient<MainWindowViewModel>();

        services.AddSingleton<DictationSessionCoordinator>();

        services.AddSingleton<ISettingsRepository, FileSettingsRepository>();
        services.AddSingleton<IAudioCaptureService, PlaceholderAudioCaptureService>();
        services.AddSingleton<ITranscriptionProvider, FakeTranscriptionProvider>();
        services.AddSingleton<ITextInjector, ClipboardTextInjector>();
        services.AddSingleton<IHotkeyService, WindowsHotkeyService>();

        return services;
    }
}
