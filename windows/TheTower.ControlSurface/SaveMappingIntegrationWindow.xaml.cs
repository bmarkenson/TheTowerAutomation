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

    private sealed record WorkspaceChoice(
        string Label,
        SaveMappingWorkspaceStatus Workspace);

    private sealed record SelectionSnapshot(
        string CandidateRecordId,
        string WorkspaceId,
        long Generation);

    private readonly ControlSurfaceApi _api;
    private SaveMappingIntegrationReview? _review;
    private SaveMappingPreparedResult? _preparedResult;
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

    private WorkspaceChoice? SelectedWorkspace =>
        WorkspaceBox.SelectedItem as WorkspaceChoice;

    private async Task RefreshCatalogAsync()
    {
        await RunAsync(async cancellationToken =>
        {
            ClearReview("Review an exact proposal before preparation.");
            var catalog = await _api.GetSaveMappingIntegrationAsync(
                cancellationToken);
            if (catalog.SchemaVersion != 1
                || catalog.Capability != "save_mapping_integration_v1")
            {
                throw new InvalidOperationException(
                    "The Linux service returned an incompatible save-mapping catalog.");
            }
            _applying = true;
            try
            {
                CandidateBox.ItemsSource = catalog.Items
                    .Select(item => new CandidateChoice(
                        SaveMappingIntegrationViewModels.CandidateLabel(item),
                        item))
                    .ToArray();
                WorkspaceBox.ItemsSource = catalog.Workspaces
                    .Select(workspace => new WorkspaceChoice(
                        SaveMappingIntegrationViewModels.WorkspaceLabel(workspace),
                        workspace))
                    .ToArray();
                CandidateBox.SelectedItem = null;
                var available = ((IEnumerable<WorkspaceChoice>)WorkspaceBox.ItemsSource)
                    .Where(choice => choice.Workspace.Available)
                    .ToArray();
                WorkspaceBox.SelectedItem = available.Length == 1
                    ? available[0]
                    : null;
            }
            finally
            {
                _applying = false;
            }
            _selectionGeneration += 1;
            CatalogStatusText.Text = catalog.Available
                ? $"{catalog.Items.Count} observation(s) · "
                    + $"{catalog.Workspaces.Count} linked feature worktree(s)"
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
        var workspace = SelectedWorkspace?.Workspace;
        CandidateDetailText.Text = candidate is null
            ? "Choose one durable observation."
            : $"{candidate.MappingId} · {candidate.State} · "
                + (string.IsNullOrWhiteSpace(candidate.Reason)
                    ? "Review pending"
                    : candidate.Reason);
        WorkspaceDetailText.Text = workspace is null
            ? "Choose the feature worktree owned by this outcome."
            : $"{workspace.PathDisplay}{Environment.NewLine}"
                + (workspace.Available
                    ? "Eligible for review and preparation."
                    : string.IsNullOrWhiteSpace(workspace.Reason)
                        ? "Unavailable."
                        : workspace.Reason);
        ReviewButton.IsEnabled = !_busy
            && candidate?.ReviewAvailable == true
            && workspace is not null;
        ReviewButton.ToolTip = candidate?.ReviewAvailable == false
            ? candidate.ReviewReason
            : "Review the exact server-generated proposal.";
    }

    private async void Review_Click(object sender, RoutedEventArgs e)
    {
        var candidate = SelectedCandidate?.Item;
        var workspace = SelectedWorkspace?.Workspace;
        if (candidate is null || workspace is null)
        {
            return;
        }
        var selection = new SelectionSnapshot(
            candidate.RecordId,
            workspace.WorkspaceId,
            _selectionGeneration);
        await RunAsync(async cancellationToken =>
        {
            var review = await _api.ReviewSaveMappingIntegrationAsync(
                new
                {
                    operation = "review",
                    candidate_record_id = candidate.RecordId,
                    workspace_id = workspace.WorkspaceId,
                },
                cancellationToken);
            if (!SelectionStillCurrent(selection)
                || !SaveMappingIntegrationViewModels.ReviewMatches(
                    review,
                    candidate.RecordId,
                    workspace.WorkspaceId))
            {
                throw new InvalidOperationException(
                    "The Linux service returned a review for a different selection.");
            }
            _review = review;
            ProposalText.Text = SaveMappingIntegrationViewModels.ProposalText(
                review);
            var availability =
                SaveMappingIntegrationViewModels.PrepareAvailability(
                    review,
                    candidate.RecordId,
                    workspace.WorkspaceId);
            PrepareButton.IsEnabled = availability.Available;
            PrepareStatusText.Text = availability.Available
                ? "Ready to prepare tracked JSON in the selected feature worktree."
                : string.IsNullOrWhiteSpace(availability.Reason)
                    ? "Preparation is unavailable."
                    : availability.Reason;
            if (review.RecoveryRequired)
            {
                ResultPanel.Visibility = Visibility.Visible;
                ResultPanel.BorderBrush = new SolidColorBrush(
                    Color.FromRgb(241, 191, 91));
                ResultText.Text = "Interrupted preparation requires recovery"
                    + Environment.NewLine
                    + (string.IsNullOrWhiteSpace(availability.Reason)
                        ? "Inspect the selected feature worktree before another action."
                        : availability.Reason);
            }
            else if (review.Prepared && review.PreparedResult is null)
            {
                throw new InvalidOperationException(
                    "The Linux service reported prepared state without its exact result.");
            }
            if (review.PreparedResult is not null)
            {
                RenderPreparedResult(
                    review.PreparedResult,
                    candidate.RecordId,
                    workspace.WorkspaceId,
                    review.ReviewedProposalFingerprint,
                    alreadyPrepared: true);
            }
        });
    }

    private async void Prepare_Click(object sender, RoutedEventArgs e)
    {
        var candidate = SelectedCandidate?.Item;
        var workspace = SelectedWorkspace?.Workspace;
        var availability = SaveMappingIntegrationViewModels.PrepareAvailability(
            _review,
            candidate?.RecordId,
            workspace?.WorkspaceId);
        if (!availability.Available
            || candidate is null
            || workspace is null
            || _review is null)
        {
            return;
        }
        var targets = SaveMappingIntegrationViewModels.Targets(_review.Proposal);
        var confirmation =
            $"Prepare this exact proposal in {workspace.Branch}?\n\n"
            + $"{workspace.PathDisplay}\n\n"
            + $"Fingerprint: {_review.ReviewedProposalFingerprint}\n"
            + $"Targets: {targets.Count}\n\n"
            + "This makes tracked JSON dirty in the feature worktree. It does "
            + "not test, commit, merge, promote, restart anything, send device "
            + "input, or change the current battle.";
        if (MessageBox.Show(
            this,
            confirmation,
            "Prepare canonical save mapping",
            MessageBoxButton.OKCancel,
            MessageBoxImage.Warning,
            MessageBoxResult.Cancel) != MessageBoxResult.OK)
        {
            return;
        }
        var reviewedFingerprint = _review.ReviewedProposalFingerprint;
        var selection = new SelectionSnapshot(
            candidate.RecordId,
            workspace.WorkspaceId,
            _selectionGeneration);
        await RunAsync(async cancellationToken =>
        {
            var result = await _api.PrepareSaveMappingIntegrationAsync(
                new
                {
                    operation = "prepare",
                    candidate_record_id = candidate.RecordId,
                    workspace_id = workspace.WorkspaceId,
                    reviewed_proposal_fingerprint = reviewedFingerprint,
                },
                cancellationToken);
            if (!SelectionStillCurrent(selection))
            {
                throw new InvalidOperationException(
                    "The GUI selection changed while preparation was in flight.");
            }
            RenderPreparedResult(
                result,
                candidate.RecordId,
                workspace.WorkspaceId,
                reviewedFingerprint,
                alreadyPrepared: false);
        }, prepareRequest: true);
    }

    private void ClearReview(string message)
    {
        _review = null;
        _preparedResult = null;
        ProposalText.Text = message;
        ResultPanel.Visibility = Visibility.Collapsed;
        ResultText.Text = "";
        PrepareButton.IsEnabled = false;
        PrepareStatusText.Text =
            "Preparation remains disabled until this exact selection is reviewed.";
    }

    private async Task RunAsync(
        Func<CancellationToken, Task> action,
        bool prepareRequest = false)
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
            _preparedResult = null;
            ResultPanel.Visibility = Visibility.Visible;
            ResultPanel.BorderBrush = new SolidColorBrush(
                Color.FromRgb(241, 191, 91));
            var code = exc is ControlSurfaceApiException apiError
                ? apiError.Code
                : "";
            var presentation = SaveMappingIntegrationViewModels.Failure(
                code,
                exc.Message,
                prepareRequest);
            ResultText.Text = presentation.Title
                + Environment.NewLine
                + presentation.Detail;
            PrepareStatusText.Text = presentation.Detail;
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void RenderSelectionButtonsOnly()
    {
        ReviewButton.IsEnabled = SelectedCandidate?.Item.ReviewAvailable == true
            && SelectedWorkspace is not null;
        var availability = SaveMappingIntegrationViewModels.PrepareAvailability(
            _review,
            SelectedCandidate?.Item.RecordId,
            SelectedWorkspace?.Workspace.WorkspaceId);
        PrepareButton.IsEnabled = availability.Available
            && _preparedResult is null;
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
        && SelectedCandidate?.Item.RecordId == selection.CandidateRecordId
        && SelectedWorkspace?.Workspace.WorkspaceId == selection.WorkspaceId;

    private void RenderPreparedResult(
        SaveMappingPreparedResult result,
        string candidateRecordId,
        string workspaceId,
        string reviewedFingerprint,
        bool alreadyPrepared)
    {
        var validation = SaveMappingIntegrationViewModels.ValidatePreparedResult(
            result,
            candidateRecordId,
            workspaceId,
            reviewedFingerprint);
        if (!validation.Valid)
        {
            throw new InvalidOperationException(validation.Reason);
        }
        _preparedResult = result;
        ResultPanel.Visibility = Visibility.Visible;
        ResultPanel.BorderBrush = new SolidColorBrush(
            Color.FromRgb(73, 214, 157));
        ResultText.Text = SaveMappingIntegrationViewModels.PreparedResultText(
            result,
            candidateRecordId,
            workspaceId,
            reviewedFingerprint);
        PrepareStatusText.Text = alreadyPrepared
            ? "Already prepared — validation, commit, and promotion remain required."
            : "Prepared — validation, commit, and promotion remain required.";
        PrepareButton.IsEnabled = false;
    }

    private void SetBusy(bool busy)
    {
        _busy = busy;
        CandidateBox.IsEnabled = !busy;
        WorkspaceBox.IsEnabled = !busy;
        CloseButton.IsEnabled = !busy;
        RefreshButton.IsEnabled = !busy;
        if (busy)
        {
            ReviewButton.IsEnabled = false;
            PrepareButton.IsEnabled = false;
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
