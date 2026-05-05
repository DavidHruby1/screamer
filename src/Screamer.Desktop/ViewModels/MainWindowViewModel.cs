using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Windows.Input;
using Screamer.Core.Abstractions;
using Screamer.Core.Dictation;
using Screamer.Core.Enums;

namespace Screamer.Desktop.ViewModels;

public sealed class MainWindowViewModel : INotifyPropertyChanged, IDisposable
{
    private readonly DictationSessionCoordinator _coordinator;
    private readonly ISettingsRepository _settingsRepository;
    private readonly IHotkeyService _hotkeyService;
    private readonly SynchronizationContext _context;

    private DictationState _state;
    private string _lastTranscript = string.Empty;
    private string _lastError = string.Empty;
    private string _hotkeyText = string.Empty;
    private string _settingsPath = string.Empty;
    private bool _isBusy;
    private CancellationTokenSource? _cts;

    public MainWindowViewModel(DictationSessionCoordinator coordinator, ISettingsRepository settingsRepository, IHotkeyService hotkeyService)
    {
        _coordinator = coordinator;
        _settingsRepository = settingsRepository;
        _hotkeyService = hotkeyService;
        _context = SynchronizationContext.Current ?? new SynchronizationContext();

        Title = "Screamer";
        State = DictationState.Idle;

        _coordinator.StateChanged += OnCoordinatorStateChanged;
        _hotkeyService.DictationStarted += OnHotkeyPressed;

        RunTestDictationCommand = new AsyncRelayCommand(RunTestDictationAsync, () => !IsBusy);
        ClearErrorCommand = new RelayCommand(ClearError);
        ExitCommand = new RelayCommand(RequestExit);
        HideCommand = new RelayCommand(RequestHide);

        _ = LoadSettingsAsync();
        _ = StartHotkeyServiceAsync();
    }

    public string Title { get; }

    public DictationState State
    {
        get => _state;
        private set
        {
            if (_state == value) return;
            _state = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(StateText));
        }
    }

    public string StateText => State switch
    {
        DictationState.Idle => "Idle",
        DictationState.Capturing => "Capturing...",
        DictationState.Transcribing => "Transcribing...",
        DictationState.Injecting => "Injecting...",
        DictationState.Error => "Error",
        _ => "Unknown"
    };

    public string LastTranscript
    {
        get => _lastTranscript;
        private set
        {
            if (_lastTranscript == value) return;
            _lastTranscript = value;
            OnPropertyChanged();
        }
    }

    public string LastError
    {
        get => _lastError;
        private set
        {
            if (_lastError == value) return;
            _lastError = value;
            OnPropertyChanged();
        }
    }

    public string HotkeyText
    {
        get => _hotkeyText;
        private set
        {
            if (_hotkeyText == value) return;
            _hotkeyText = value;
            OnPropertyChanged();
        }
    }

    public string SettingsPath
    {
        get => _settingsPath;
        private set
        {
            if (_settingsPath == value) return;
            _settingsPath = value;
            OnPropertyChanged();
        }
    }

    public bool IsBusy
    {
        get => _isBusy;
        private set
        {
            if (_isBusy == value) return;
            _isBusy = value;
            OnPropertyChanged();
            CommandManager.InvalidateRequerySuggested();
        }
    }

    public ObservableCollection<string> DiagnosticsLog { get; } = [];

    public ICommand RunTestDictationCommand { get; }
    public ICommand ClearErrorCommand { get; }
    public ICommand ExitCommand { get; }
    public ICommand HideCommand { get; }

    public event Action? ExitRequested;
    public event Action? HideRequested;

    public event PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }

    private async Task LoadSettingsAsync()
    {
        try
        {
            var settings = await _settingsRepository.LoadAsync(CancellationToken.None);
            HotkeyText = settings.Hotkey.Gesture;
            SettingsPath = "Loaded";
            AppendLog("Settings loaded.");
        }
        catch (Exception ex)
        {
            SettingsPath = "Error loading";
            AppendLog($"Settings load failed: {ex.Message}");
        }
    }

    private async Task StartHotkeyServiceAsync()
    {
        try
        {
            await _hotkeyService.StartAsync(CancellationToken.None);
            AppendLog("Hotkey registered.");
        }
        catch (Exception ex)
        {
            LastError = $"Hotkey registration failed: {ex.Message}";
            AppendLog($"Hotkey failed: {ex.Message}");
        }
    }

    private void OnHotkeyPressed(object? sender, EventArgs e)
    {
        _context.Post(_ => ToggleDictationFromHotkey(), null);
    }

    private void ToggleDictationFromHotkey()
    {
        if (IsBusy && _cts is not null)
        {
            _cts.Cancel();
            AppendLog("Hotkey cancelled dictation.");
            return;
        }

        _ = RunTestDictationAsync();
    }

    private async Task RunTestDictationAsync()
    {
        LastError = string.Empty;
        AppendLog("Starting dictation...");

        IsBusy = true;
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = new CancellationTokenSource();

        try
        {
            var result = await _coordinator.RunOnceAsync(_cts.Token);
            LastTranscript = result.Text;
            AppendLog($"Dictation complete: \"{result.Text}\"");
        }
        catch (OperationCanceledException)
        {
            AppendLog("Dictation cancelled.");
        }
        catch (Exception ex)
        {
            LastError = ex.Message;
            AppendLog($"Dictation failed: {ex.Message}");
        }
        finally
        {
            _cts?.Dispose();
            _cts = null;
            IsBusy = false;
        }
    }

    private void OnCoordinatorStateChanged(DictationState state)
    {
        _context.Post(_ => State = state, null);
    }

    private void ClearError()
    {
        LastError = string.Empty;
        AppendLog("Error cleared.");
    }

    private void RequestExit()
    {
        ExitRequested?.Invoke();
    }

    private void RequestHide()
    {
        HideRequested?.Invoke();
    }

    private void AppendLog(string message)
    {
        var timestamp = DateTime.Now.ToString("HH:mm:ss");
        DiagnosticsLog.Add($"[{timestamp}] {message}");
    }

    public void Dispose()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _coordinator.StateChanged -= OnCoordinatorStateChanged;
        _hotkeyService.DictationStarted -= OnHotkeyPressed;
    }
}
