using System.ComponentModel;
using System.Windows;
using Screamer.Desktop.Tray;
using Screamer.Desktop.ViewModels;
using WpfApplication = System.Windows.Application;

namespace Screamer.Desktop;

public partial class MainWindow : Window
{
    private readonly TrayIconService _tray;

    public MainWindow(MainWindowViewModel viewModel)
    {
        InitializeComponent();
        DataContext = viewModel;

        _tray = new TrayIconService();
        _tray.ShowRequested += () =>
        {
            Show();
            WindowState = WindowState.Normal;
            Activate();
        };
        _tray.RunDictationRequested += () =>
        {
            if (viewModel.RunTestDictationCommand.CanExecute(null))
                viewModel.RunTestDictationCommand.Execute(null);
        };
        _tray.ExitRequested += () => WpfApplication.Current.Shutdown();

        viewModel.ExitRequested += () => WpfApplication.Current.Shutdown();
        viewModel.HideRequested += () => Hide();
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        e.Cancel = true;
        Hide();
        base.OnClosing(e);
    }

    protected override void OnClosed(EventArgs e)
    {
        _tray.Dispose();
        base.OnClosed(e);
    }
}
