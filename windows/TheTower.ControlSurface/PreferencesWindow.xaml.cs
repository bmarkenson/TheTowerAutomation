using System.Globalization;
using System.Windows;
using System.Windows.Media;
using TheTower.TunnelProtocol;

namespace TheTower.ControlSurface;

public partial class PreferencesWindow : Window
{
    public PreferencesResult? Result { get; private set; }
    public bool ResetLayoutRequested { get; private set; }

    public PreferencesWindow(ClientSettings settings, string inMemoryToken)
    {
        InitializeComponent();
        BaseUrlBox.Text = settings.BaseUrl;
        TokenBox.Password = inMemoryToken;
        SshDestinationBox.Text = settings.SshDestination;
        LocalTunnelPortBox.Text = settings.LocalTunnelPort.ToString(
            CultureInfo.InvariantCulture);
        RemoteApiPortBox.Text = settings.RemoteApiPort.ToString(
            CultureInfo.InvariantCulture);
        WindowsBlueStacksAdbPortBox.Text =
            settings.WindowsBlueStacksAdbPort.ToString(
                CultureInfo.InvariantCulture);
        LinuxAdbForwardPortBox.Text =
            settings.LinuxAdbForwardPort.ToString(
                CultureInfo.InvariantCulture);
        HostPerformanceSamplingBox.IsChecked =
            settings.HostPerformanceSamplingEnabled;
    }

    private void ResetLayout_Click(object sender, RoutedEventArgs e)
    {
        ResetLayoutRequested = true;
        ResetLayoutButton.IsEnabled = false;
        ResetLayoutStateText.Text =
            "Dashboard page, System page, and optional detail expansion will "
            + "reset when preferences are saved.";
        ResetLayoutStateText.Foreground =
            new SolidColorBrush(Color.FromRgb(241, 191, 91));
    }

    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var baseUrl = NormalizeApiUrl(BaseUrlBox.Text);
            var configuration = TunnelHostConfigurationValidator.Validate(
                new TunnelHostConfiguration
                {
                    SshDestination = SshDestinationBox.Text,
                    LocalApiPort = ParsePort(
                        LocalTunnelPortBox.Text,
                        "Local API port"),
                    RemoteApiPort = ParsePort(
                        RemoteApiPortBox.Text,
                        "Remote API port"),
                    WindowsBlueStacksAdbPort = ParsePort(
                        WindowsBlueStacksAdbPortBox.Text,
                        "Windows BlueStacks ADB port"),
                    LinuxAdbPort = ParsePort(
                        LinuxAdbForwardPortBox.Text,
                        "Linux ADB forward port"),
                },
                requireDestination: false);
            Result = new PreferencesResult(
                baseUrl,
                TokenBox.Password.Trim(),
                configuration,
                HostPerformanceSamplingBox.IsChecked == true);
            DialogResult = true;
        }
        catch (ArgumentException exc)
        {
            MessageBox.Show(
                this,
                exc.Message,
                "Invalid preferences",
                MessageBoxButton.OK,
                MessageBoxImage.Warning);
        }
    }

    private static string NormalizeApiUrl(string value)
    {
        var normalized = value.Trim().TrimEnd('/');
        if (!Uri.TryCreate(normalized, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp
                && uri.Scheme != Uri.UriSchemeHttps))
        {
            throw new ArgumentException(
                "API URL must be an absolute HTTP or HTTPS URL.");
        }
        return uri.ToString().TrimEnd('/');
    }

    private static int ParsePort(string value, string label)
    {
        if (!int.TryParse(
                value.Trim(),
                NumberStyles.None,
                CultureInfo.InvariantCulture,
                out var port)
            || port is < 1 or > 65535)
        {
            throw new ArgumentException(
                $"{label} must be between 1 and 65535.");
        }
        return port;
    }
}

public sealed record PreferencesResult(
    string BaseUrl,
    string InMemoryToken,
    TunnelHostConfiguration TunnelConfiguration,
    bool HostPerformanceSamplingEnabled);
