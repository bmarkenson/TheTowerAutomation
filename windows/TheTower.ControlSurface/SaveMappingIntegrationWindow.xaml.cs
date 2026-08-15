using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace TheTower.ControlSurface;

public partial class SaveMappingIntegrationWindow : Window
{
    private sealed record CandidateChoice(
        string Label,
        SaveMappingIntegrationItem Item);

    private sealed record SelectionSnapshot(
        string CandidateRecordId,
        long Generation);

    private readonly ControlSurfaceApi _api;
    private SaveMappingIntegrationReview? _review;
    private SaveMappingIntegratedResult? _integratedResult;
    private bool _busy;
    private bool _applying;
    private long _selectionGeneration;

    public SaveMappingIntegrationWindow(ControlSurfaceApi api)
    {
        InitializeComponent();
        _api = api;
        Loaded += async (_, _) => await RefreshCatalogAsync();
        Closing += Window_Closing;
    }

    private CandidateChoice? SelectedCandidate =>
        CandidateBox.SelectedItem as CandidateChoice;

    private async Task RefreshCatalogAsync()
    {
        await RunAsync(async cancellationToken =>
        {
            ClearReview("Review an exact proposal before staging.");
            var catalog = await _api.GetSaveMappingIntegrationAsync(
                cancellationToken);
            if (catalog.SchemaVersion != 3
                || catalog.Capability != "save_mapping_staged_candidate_v1")
            {
                throw new InvalidOperationException(
                    "The Linux service returned an incompatible save-mapping catalog.");
            }
            _applying = true;
            try
            {
                var choices = catalog.Items
                    .Select(item => new CandidateChoice(
                        SaveMappingIntegrationViewModels.CandidateLabel(item),
                        item))
                    .ToArray();
                CandidateBox.ItemsSource = choices;
                CandidateBox.SelectedItem = choices.FirstOrDefault(choice =>
                    choice.Item.RecordId
                    == catalog.Transaction?.CandidateRecordId);
            }
            finally
            {
                _applying = false;
            }
            _selectionGeneration += 1;
            RepositoryDetailText.Text =
                SaveMappingIntegrationViewModels.RepositoryText(
                    catalog.Repository);
            var readiness = catalog.Repository?.IntegrationAvailable is true
                ? "main/private staging eligible"
                : "private staging unavailable";
            CatalogStatusText.Text = catalog.Available
                ? !string.IsNullOrWhiteSpace(catalog.Transaction?.Reason)
                    ? catalog.Transaction.Reason
                    : $"{catalog.Items.Count} observation(s) · {readiness}"
                : string.IsNullOrWhiteSpace(catalog.Reason)
                    ? "Save-mapping integration catalog is unavailable."
                    : catalog.Reason;
            RenderSelection();
        });
    }

    private void Selection_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_applying || _busy)
        {
            return;
        }
        _selectionGeneration += 1;
        RenderSelection();
    }

    private void RenderSelection()
    {
        ClearReview("Selection changed. Review the exact proposal again.");
        var candidate = SelectedCandidate?.Item;
        CandidateDetailText.Text = candidate is null
            ? "Choose one durable observation."
            : SaveMappingIntegrationViewModels.CandidateDetail(candidate);
        ReviewButton.IsEnabled = !_busy
            && candidate?.ReviewAvailable == true;
        ReviewButton.ToolTip = candidate?.ReviewAvailable == false
            ? candidate.ReviewReason
            : "Review the exact server-generated proposal.";
        CopyAgentReviewButton.Visibility = string.IsNullOrWhiteSpace(
            candidate?.AgentReviewPrompt)
                ? Visibility.Collapsed
                : Visibility.Visible;
        CopyAgentReviewButton.IsEnabled = !_busy
            && !string.IsNullOrWhiteSpace(candidate?.AgentReviewPrompt);
        DismissButton.IsEnabled = !_busy
            && candidate?.DismissAvailable == true;
        DismissButton.ToolTip = candidate?.DismissAvailable == false
            ? candidate.DismissReason
            : "Hide this observation from the active queue while preserving its receipt.";
        if (candidate?.ReviewAvailable == false)
        {
            ProposalText.Text =
                SaveMappingIntegrationViewModels.NonReviewableProposalText(
                    candidate);
            IntegrateStatusText.Text = string.IsNullOrWhiteSpace(
                candidate.NextAction)
                    ? "The exact proposal must be resolved before staging."
                    : candidate.NextAction;
        }
        else
        {
            IntegrateStatusText.Text = candidate is null
                ? "Choose an observation to see its available actions."
                : "Review this exact candidate before staging, or dismiss it if it is incorrect.";
        }
    }

    private void CopyAgentReview_Click(object sender, RoutedEventArgs e)
    {
        var prompt = SelectedCandidate?.Item.AgentReviewPrompt;
        if (string.IsNullOrWhiteSpace(prompt))
        {
            return;
        }
        try
        {
            Clipboard.SetText(prompt);
            IntegrateStatusText.Text = "Agent-review request copied to the clipboard.";
        }
        catch (Exception exc)
        {
            ProposalText.Text = prompt;
            ProposalText.Focus();
            ProposalText.SelectAll();
            ResultPanel.Visibility = Visibility.Visible;
            ResultPanel.BorderBrush = new SolidColorBrush(
                Color.FromRgb(241, 191, 91));
            ResultText.Text = "Clipboard unavailable"
                + Environment.NewLine
                + exc.Message
                + Environment.NewLine
                + "The request remains selected in the proposal panel for manual copying.";
        }
    }

    private async void Dismiss_Click(object sender, RoutedEventArgs e)
    {
        var candidate = SelectedCandidate?.Item;
        if (candidate?.DismissAvailable != true)
        {
            return;
        }
        var confirmation =
            "Dismiss this exact observation from the active review queue?\n\n"
            + candidate.RecordId
            + "\n\nThe original durable receipt will be preserved, together "
            + "with an append-only dismissal record. This does not change "
            + "canonical mappings, Git refs, main, runtime authority, device "
            + "input, or the current battle.";
        if (MessageBox.Show(
            this,
            confirmation,
            "Dismiss save-mapping observation",
            MessageBoxButton.OKCancel,
            MessageBoxImage.Warning,
            MessageBoxResult.Cancel) != MessageBoxResult.OK)
        {
            return;
        }
        var selection = new SelectionSnapshot(
            candidate.RecordId,
            _selectionGeneration);
        SaveMappingDismissedResult? dismissed = null;
        await RunAsync(async cancellationToken =>
        {
            var result = await _api.DismissSaveMappingObservationAsync(
                new
                {
                    operation = "dismiss",
                    candidate_record_id = candidate.RecordId,
                },
                cancellationToken);
            if (!SelectionStillCurrent(selection))
            {
                throw new InvalidOperationException(
                    "The GUI selection changed while dismissal was in flight.");
            }
            var validation =
                SaveMappingIntegrationViewModels.ValidateDismissedResult(
                    result,
                    candidate.RecordId);
            if (!validation.Valid)
            {
                throw new InvalidOperationException(validation.Reason);
            }
            dismissed = result;
        }, dismissalRequest: true);
        if (dismissed is not { } dismissalResult)
        {
            return;
        }
        await RefreshCatalogAsync();
        ResultPanel.Visibility = Visibility.Visible;
        ResultPanel.BorderBrush = new SolidColorBrush(
            Color.FromRgb(73, 214, 157));
        ResultText.Text =
            SaveMappingIntegrationViewModels.DismissedResultText(
                dismissalResult);
        IntegrateStatusText.Text =
            "Observation removed from the active queue; its durable evidence was preserved.";
    }

    private async void Review_Click(object sender, RoutedEventArgs e)
    {
        var candidate = SelectedCandidate?.Item;
        if (candidate is null)
        {
            return;
        }
        var selection = new SelectionSnapshot(
            candidate.RecordId,
            _selectionGeneration);
        await RunAsync(async cancellationToken =>
        {
            var review = await _api.ReviewSaveMappingIntegrationAsync(
                new
                {
                    operation = "review",
                    candidate_record_id = candidate.RecordId,
                },
                cancellationToken);
            if (!SelectionStillCurrent(selection)
                || !SaveMappingIntegrationViewModels.ReviewMatches(
                    review,
                    candidate.RecordId))
            {
                throw new InvalidOperationException(
                    "The Linux service returned a review for a different selection.");
            }
            _review = review;
            ProposalText.Text = SaveMappingIntegrationViewModels.ProposalText(
                review);
            var availability =
                SaveMappingIntegrationViewModels.IntegrateAvailability(
                    review,
                    candidate.RecordId);
            IntegrateButton.IsEnabled = availability.Available;
            IntegrateStatusText.Text = availability.Available
                ? "Ready to create one verified staged commit without moving main."
                : string.IsNullOrWhiteSpace(availability.Reason)
                    ? "Private-ref staging is unavailable."
                    : availability.Reason;
            if (review.RecoveryRequired)
            {
                ResultPanel.Visibility = Visibility.Visible;
                ResultPanel.BorderBrush = new SolidColorBrush(
                    Color.FromRgb(241, 191, 91));
                ResultText.Text = "Interrupted integration requires recovery"
                    + Environment.NewLine
                    + (string.IsNullOrWhiteSpace(availability.Reason)
                        ? "Inspect main, the private staging ref, and the durable transaction before another action."
                        : availability.Reason);
            }
        });
    }

    private async void Integrate_Click(object sender, RoutedEventArgs e)
    {
        var candidate = SelectedCandidate?.Item;
        var availability = SaveMappingIntegrationViewModels.IntegrateAvailability(
            _review,
            candidate?.RecordId);
        if (!availability.Available || candidate is null || _review is null)
        {
            return;
        }
        var targets = SaveMappingIntegrationViewModels.Targets(_review.Proposal);
        var confirmation =
            (_review.RecoveryRequired
                ? "Recover and verify this exact durable integration?\n\n"
                : "Stage this exact proposal for production promotion?\n\n")
            + $"{_review.Repository.StagingRef}\n\n"
            + $"Fingerprint: {_review.ReviewedProposalFingerprint}\n"
            + $"Targets: {targets.Count}\n\n"
            + (_review.RecoveryRequired
                ? "This retries only the durable reviewed identity and verifies "
                    + "exact Git refs and mappings. It does not create a second "
                    + "commit, promote, restart services, send device input, change "
                    + "runtime authority, or alter the current battle."
                : "This creates one verified child of current main under a private "
                    + "staging ref. It does not move main, touch the production index "
                    + "or worktree, restart services, send device input, change runtime "
                    + "authority, or alter the current battle.");
        if (MessageBox.Show(
            this,
            confirmation,
            "Stage canonical save mapping",
            MessageBoxButton.OKCancel,
            MessageBoxImage.Warning,
            MessageBoxResult.Cancel) != MessageBoxResult.OK)
        {
            return;
        }
        var reviewedFingerprint = _review.ReviewedProposalFingerprint;
        var reviewed = _review;
        var selection = new SelectionSnapshot(
            candidate.RecordId,
            _selectionGeneration);
        await RunAsync(async cancellationToken =>
        {
            var result = await _api.IntegrateSaveMappingAsync(
                new
                {
                    operation = "stage",
                    candidate_record_id = candidate.RecordId,
                    reviewed_proposal_fingerprint = reviewedFingerprint,
                },
                cancellationToken);
            if (!SelectionStillCurrent(selection))
            {
                throw new InvalidOperationException(
                    "The GUI selection changed while integration was in flight.");
            }
            RenderIntegratedResult(
                result,
                reviewed);
        }, integrateRequest: true);
    }

    private void ClearReview(string message)
    {
        _review = null;
        _integratedResult = null;
        ProposalText.Text = message;
        ResultPanel.Visibility = Visibility.Collapsed;
        ResultText.Text = "";
        IntegrateButton.IsEnabled = false;
        IntegrateStatusText.Text =
            "Staging remains disabled until this exact candidate is reviewed.";
    }

    private async Task RunAsync(
        Func<CancellationToken, Task> action,
        bool integrateRequest = false,
        bool dismissalRequest = false)
    {
        if (_busy)
        {
            return;
        }
        SetBusy(true);
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(125));
            await action(cancellation.Token);
        }
        catch (Exception exc)
        {
            _review = null;
            _integratedResult = null;
            ResultPanel.Visibility = Visibility.Visible;
            ResultPanel.BorderBrush = new SolidColorBrush(
                Color.FromRgb(241, 191, 91));
            var code = exc is ControlSurfaceApiException apiError
                ? apiError.Code
                : "";
            var presentation = SaveMappingIntegrationViewModels.Failure(
                code,
                exc.Message,
                integrateRequest,
                dismissalRequest);
            ResultText.Text = presentation.Title
                + Environment.NewLine
                + presentation.Detail;
            IntegrateStatusText.Text = presentation.Detail;
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void RenderSelectionButtonsOnly()
    {
        ReviewButton.IsEnabled = SelectedCandidate?.Item.ReviewAvailable == true;
        CopyAgentReviewButton.IsEnabled = !string.IsNullOrWhiteSpace(
            SelectedCandidate?.Item.AgentReviewPrompt);
        DismissButton.IsEnabled =
            SelectedCandidate?.Item.DismissAvailable == true;
        var availability = SaveMappingIntegrationViewModels.IntegrateAvailability(
            _review,
            SelectedCandidate?.Item.RecordId);
        IntegrateButton.IsEnabled = availability.Available
            && _integratedResult is null;
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) =>
        await RefreshCatalogAsync();

    private void Close_Click(object sender, RoutedEventArgs e)
    {
        if (!_busy)
        {
            Close();
        }
    }

    private bool SelectionStillCurrent(SelectionSnapshot selection) =>
        selection.Generation == _selectionGeneration
        && SelectedCandidate?.Item.RecordId == selection.CandidateRecordId;

    private void RenderIntegratedResult(
        SaveMappingIntegratedResult result,
        SaveMappingIntegrationReview review)
    {
        var validation = SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            review);
        if (!validation.Valid)
        {
            throw new InvalidOperationException(validation.Reason);
        }
        _integratedResult = result;
        ResultPanel.Visibility = Visibility.Visible;
        ResultPanel.BorderBrush = new SolidColorBrush(
            Color.FromRgb(73, 214, 157));
        ResultText.Text = SaveMappingIntegrationViewModels.IntegratedResultText(
            result,
            review);
        IntegrateStatusText.Text =
            result.Promoted is true
                ? "Deployed — a fresh stable decode remains pending."
                : "Staged privately — production promotion and a fresh stable decode remain pending.";
        IntegrateButton.IsEnabled = false;
    }

    private void SetBusy(bool busy)
    {
        _busy = busy;
        CandidateBox.IsEnabled = !busy;
        CloseButton.IsEnabled = !busy;
        RefreshButton.IsEnabled = !busy;
        if (busy)
        {
            ReviewButton.IsEnabled = false;
            CopyAgentReviewButton.IsEnabled = false;
            DismissButton.IsEnabled = false;
            IntegrateButton.IsEnabled = false;
        }
        else
        {
            RenderSelectionButtonsOnly();
        }
    }

    private void Window_Closing(object? sender, CancelEventArgs e)
    {
        if (_busy)
        {
            e.Cancel = true;
        }
    }
}
