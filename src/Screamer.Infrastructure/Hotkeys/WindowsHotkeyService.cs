using System.Runtime.InteropServices;
using System.Windows.Interop;
using Screamer.Core.Abstractions;
using Screamer.Core.Models;

namespace Screamer.Infrastructure.Hotkeys;

public sealed class WindowsHotkeyService : IHotkeyService, IDisposable
{
    private const int WM_HOTKEY = 0x0312;
    private const uint MOD_ALT = 0x0001;
    private const uint MOD_CONTROL = 0x0002;
    private const uint MOD_SHIFT = 0x0004;
    private const uint MOD_WIN = 0x0008;
    private const uint VK_SPACE = 0x20;

    private const int HotkeyId = 1;

    private readonly ISettingsRepository _settingsRepository;

    private HwndSource? _source;
    private bool _disposed;

    public WindowsHotkeyService(ISettingsRepository settingsRepository)
    {
        _settingsRepository = settingsRepository;
    }

    public event EventHandler? DictationStarted;

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Unregister();

        var settings = await _settingsRepository.LoadAsync(cancellationToken);
        var gesture = settings.Hotkey.Gesture;
        var parsed = ParseGesture(gesture);

        var sourceParams = new HwndSourceParameters("ScreamerHotkeyWindow")
        {
            WindowStyle = 0,
            Width = 0,
            Height = 0,
            ParentWindow = (IntPtr)(-3)
        };

        _source = new HwndSource(sourceParams);
        _source.AddHook(WndProc);

        if (!RegisterHotKey(_source.Handle, HotkeyId, parsed.modifiers, parsed.virtualKey))
        {
            _source.Dispose();
            _source = null;
            throw new InvalidOperationException($"Failed to register hotkey '{gesture}'.");
        }
    }

    public Task StopAsync(CancellationToken cancellationToken)
    {
        Unregister();
        return Task.CompletedTask;
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        Unregister();
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == WM_HOTKEY && (int)wParam == HotkeyId)
        {
            DictationStarted?.Invoke(this, EventArgs.Empty);
            handled = true;
        }
        return IntPtr.Zero;
    }

    private void Unregister()
    {
        if (_source is not null)
        {
            if (_source.Handle != IntPtr.Zero)
                UnregisterHotKey(_source.Handle, HotkeyId);
            _source.Dispose();
            _source = null;
        }
    }

    private static (uint modifiers, uint virtualKey) ParseGesture(string gesture)
    {
        uint mods = 0;
        uint vk = 0;
        var parts = gesture.Split('+');

        foreach (var part in parts)
        {
            switch (part.Trim().ToLowerInvariant())
            {
                case "ctrl":
                    mods |= MOD_CONTROL;
                    break;
                case "alt":
                    mods |= MOD_ALT;
                    break;
                case "shift":
                    mods |= MOD_SHIFT;
                    break;
                case "win":
                    mods |= MOD_WIN;
                    break;
                case "space":
                    vk = VK_SPACE;
                    break;
                default:
                    var key = part.Trim();
                    if (key.Length is >= 1 and <= 2)
                        vk = char.ToUpperInvariant(key[0]);
                    break;
            }
        }

        if (mods == 0 || vk == 0)
            throw new InvalidOperationException($"Invalid hotkey gesture '{gesture}'.");

        return (mods, vk);
    }

    [DllImport("user32.dll")]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll")]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);
}
