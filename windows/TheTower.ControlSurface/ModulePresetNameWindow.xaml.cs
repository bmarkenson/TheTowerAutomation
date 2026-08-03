using System.Text.RegularExpressions;
using System.Windows;

namespace TheTower.ControlSurface;

public partial class ModulePresetNameWindow : Window
{
    private static readonly Regex SafeId = new(
        "^[a-z][a-z0-9_]{2,47}$",
        RegexOptions.CultureInvariant);

    public ModulePresetNameWindow(
        string explanation,
        string suggestedDisplayName,
        string suggestedId)
    {
        InitializeComponent();
        ExplanationText.Text = explanation;
        DisplayNameBox.Text = suggestedDisplayName;
        PresetIdBox.Text = suggestedId;
        Loaded += (_, _) =>
        {
            DisplayNameBox.Focus();
            DisplayNameBox.SelectAll();
        };
    }

    public string PresetDisplayName => DisplayNameBox.Text.Trim();
    public string PresetId => PresetIdBox.Text.Trim();

    private void Create_Click(object sender, RoutedEventArgs e)
    {
        if (PresetDisplayName.Length == 0 || PresetDisplayName.Length > 80)
        {
            ValidationText.Text =
                "Display name is required and must be at most 80 characters.";
            DisplayNameBox.Focus();
            return;
        }
        if (!SafeId.IsMatch(PresetId))
        {
            ValidationText.Text =
                "Stable ID must use 3–48 lowercase letters, digits, or underscores and start with a letter.";
            PresetIdBox.Focus();
            return;
        }
        DialogResult = true;
    }
}
