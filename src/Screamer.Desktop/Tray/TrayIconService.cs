using System.Windows.Forms;

namespace Screamer.Desktop.Tray;

public sealed class TrayIconService : IDisposable
{
    private readonly NotifyIcon _notifyIcon;
    private bool _disposed;

    public event Action? ShowRequested;
    public event Action? RunDictationRequested;
    public event Action? ExitRequested;

    public TrayIconService()
    {
        _notifyIcon = new NotifyIcon
        {
            Icon = SystemIcons.Application,
            Text = "Screamer",
            Visible = true,
            ContextMenuStrip = new ContextMenuStrip()
        };

        _notifyIcon.ContextMenuStrip.Items.Add("Show", null, (_, _) => ShowRequested?.Invoke());
        _notifyIcon.ContextMenuStrip.Items.Add("Run Test Dictation", null, (_, _) => RunDictationRequested?.Invoke());
        _notifyIcon.ContextMenuStrip.Items.Add(new ToolStripSeparator());
        _notifyIcon.ContextMenuStrip.Items.Add("Exit", null, (_, _) => ExitRequested?.Invoke());
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _notifyIcon.Visible = false;
        _notifyIcon.Dispose();
    }
}
