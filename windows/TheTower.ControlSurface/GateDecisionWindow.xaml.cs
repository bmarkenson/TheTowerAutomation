using System.Windows;

namespace TheTower.ControlSurface;

public partial class GateDecisionWindow : Window
{
    public GateDecisionOption? SelectedOption { get; private set; }

    public GateDecisionWindow(GateDecisionStatus decision)
    {
        InitializeComponent();
        CheckText.Text = $"Check: {decision.CheckId} ({decision.Phase})";
        ReasonText.Text = decision.Reason;
        ExpectedText.Text = string.IsNullOrWhiteSpace(decision.Expected)
            ? ""
            : $"Required: {decision.Expected}";
        OptionsList.ItemsSource = decision.Options;
        if (decision.Options.Count > 0)
        {
            OptionsList.SelectedIndex = 0;
        }
    }

    private void Apply_Click(object sender, RoutedEventArgs e)
    {
        if (OptionsList.SelectedItem is not GateDecisionOption selected)
        {
            MessageBox.Show(
                this,
                "Select one available direction.",
                "Startup gate",
                MessageBoxButton.OK,
                MessageBoxImage.Information);
            return;
        }
        SelectedOption = selected;
        DialogResult = true;
    }

    private void Later_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }
}
