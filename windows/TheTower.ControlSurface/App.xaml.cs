using System.Runtime.InteropServices;
using System.Windows;

namespace TheTower.ControlSurface;

public partial class App : Application
{
    private const string InstanceMutexName =
        @"Local\TheTower.ControlSurface.2CF9B6E2-AD66-46A7-A4E7-01D68B7C50D2";
    private const string MainWindowTitle = "TheTower Control Surface";
    private const int RestoreWindow = 9;

    private Mutex? _instanceMutex;
    private bool _ownsInstanceMutex;

    protected override void OnStartup(StartupEventArgs e)
    {
        _instanceMutex = new Mutex(
            initiallyOwned: true,
            InstanceMutexName,
            out var createdNew);
        _ownsInstanceMutex = createdNew;
        if (!createdNew)
        {
            ActivateExistingMainWindow();
            Shutdown();
            return;
        }

        base.OnStartup(e);
        var mainWindow = new MainWindow();
        MainWindow = mainWindow;
        mainWindow.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        if (_ownsInstanceMutex)
        {
            _instanceMutex?.ReleaseMutex();
        }
        _instanceMutex?.Dispose();
        base.OnExit(e);
    }

    private static void ActivateExistingMainWindow()
    {
        nint handle = 0;
        for (var attempt = 0; attempt < 20 && handle == 0; attempt++)
        {
            handle = FindWindow(null, MainWindowTitle);
            if (handle == 0)
            {
                Thread.Sleep(50);
            }
        }

        if (handle == 0)
        {
            MessageBox.Show(
                "TheTower Control Surface is already starting or running.",
                MainWindowTitle,
                MessageBoxButton.OK,
                MessageBoxImage.Information);
            return;
        }

        if (IsIconic(handle))
        {
            ShowWindow(handle, RestoreWindow);
        }
        if (!SetForegroundWindow(handle))
        {
            FlashWindow(handle, invert: true);
        }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern nint FindWindow(string? className, string windowName);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsIconic(nint window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindow(nint window, int command);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(nint window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool FlashWindow(nint window, bool invert);
}
