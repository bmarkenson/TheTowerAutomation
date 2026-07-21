using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Windows;

namespace TheTower.ControlSurface;

public partial class ConfigureRunWindow : Window
{
    private readonly ObservableCollection<ConfigureRunCheckItem> _checks = [];

    public IReadOnlyList<string> SelectedSkipIds => _checks
        .Where(check => check.Skip)
        .Select(check => check.Id)
        .ToList();

    public ConfigureRunWindow(
        StartupGateContext context,
        IReadOnlyDictionary<string, StartupGateWaiverStatus> staged)
    {
        InitializeComponent();
        StrategyText.Text = $"Strategy: {context.Strategy}";
        foreach (var check in context.Checks)
        {
            var isStaged = staged.TryGetValue(check.Id, out var waiver)
                && string.Equals(
                    waiver.Strategy,
                    context.Strategy,
                    StringComparison.OrdinalIgnoreCase);
            _checks.Add(new ConfigureRunCheckItem(
                check.Id,
                check.Label,
                string.IsNullOrWhiteSpace(check.Expected)
                    ? "Uses the strategy's required value."
                    : $"Required by default: {check.Expected}",
                isStaged));
        }
        ChecksList.ItemsSource = _checks;
    }

    private void Defaults_Click(object sender, RoutedEventArgs e)
    {
        foreach (var check in _checks)
        {
            check.Skip = false;
        }
    }

    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = true;
    }
}

public sealed class ConfigureRunCheckItem : INotifyPropertyChanged
{
    private bool _skip;

    public ConfigureRunCheckItem(
        string id,
        string label,
        string expectedText,
        bool skip)
    {
        Id = id;
        Label = label;
        ExpectedText = expectedText;
        _skip = skip;
    }

    public string Id { get; }
    public string Label { get; }
    public string ExpectedText { get; }

    public bool Skip
    {
        get => _skip;
        set
        {
            if (_skip == value)
            {
                return;
            }
            _skip = value;
            PropertyChanged?.Invoke(
                this,
                new PropertyChangedEventArgs(nameof(Skip)));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
}
