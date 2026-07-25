using System.Windows;

namespace TheTower.ControlSurface;

public partial class TournamentLaunchWindow : Window
{
    public string? Decision { get; private set; }

    public TournamentLaunchWindow(
        ExclusiveValidationReceiptStatus receipt,
        bool canStart)
    {
        InitializeComponent();
        var policy = receipt.LaunchPolicy;
        HeadingText.Text = string.IsNullOrWhiteSpace(policy?.PromptTitle)
            ? "Tournament validation passed"
            : policy.PromptTitle;
        MessageText.Text = string.IsNullOrWhiteSpace(policy?.PromptMessage)
            ? "Start the Tournament now?"
            : policy.PromptMessage;
        ReminderText.Text = string.IsNullOrWhiteSpace(policy?.Reminder)
            ? "When the Tournament battle begins, set Target Priorities for "
                + "the current Tournament Battle Conditions."
            : policy.Reminder;
        StartButton.IsEnabled = canStart;
        if (!canStart)
        {
            DispositionText.Text +=
                " Start becomes available with a compatible active RUNNING "
                + "runtime and fresh Home or Tournament-entry evidence.";
        }
    }

    private void Start_Click(object sender, RoutedEventArgs e)
    {
        Decision = "start";
        DialogResult = true;
    }

    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        Decision = "cancel";
        DialogResult = true;
    }
}
