using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace TheTower.ControlSurface;

public sealed class StrategyRestoredEventArgs(
    string strategyId,
    int logicalVersion,
    bool? useSucceeded = null,
    string? useMessage = null) : EventArgs
{
    public string StrategyId { get; } = strategyId;
    public int LogicalVersion { get; } = logicalVersion;
    public bool? UseSucceeded { get; } = useSucceeded;
    public string? UseMessage { get; } = useMessage;
}

public partial class StrategyHistoryWindow : Window
{
    private readonly ControlSurfaceApi _api;
    private readonly Func<StrategyPublicationNotice, Task<StrategyPublicationUseResult>>?
        _publishedStrategyHandler;
    private StrategyHistoryCatalogResponse? _catalog;
    private StrategyHistoryLineage? _lineage;
    private StrategyRevisionSummary? _revision;
    private StrategyAuthoringMutationResponse? _review;
    private bool _busy;
    private bool _changingSelection;

    public StrategyHistoryWindow(ControlSurfaceApi api)
        : this(api, null)
    {
    }

    internal StrategyHistoryWindow(
        ControlSurfaceApi api,
        Func<StrategyPublicationNotice, Task<StrategyPublicationUseResult>>?
            publishedStrategyHandler)
    {
        InitializeComponent();
        _api = api;
        _publishedStrategyHandler = publishedStrategyHandler;
        Loaded += async (_, _) => await LoadHistoryAsync();
    }

    public event EventHandler<StrategyRestoredEventArgs>? StrategyRestored;

    private async Task LoadHistoryAsync(
        string? selectStrategyId = null,
        int? selectVersion = null)
    {
        SetBusy(true, "Loading immutable Strategy history...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(30));
            ApplyHistory(
                await _api.GetStrategyHistoryAsync(cancellation.Token),
                selectStrategyId,
                selectVersion);
        }
        catch (Exception exc)
        {
            ShowFailure(exc.Message);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void ApplyHistory(
        StrategyHistoryCatalogResponse catalog,
        string? selectStrategyId,
        int? selectVersion)
    {
        _catalog = catalog;
        _changingSelection = true;
        try
        {
            LineagesList.ItemsSource = catalog.Lineages;
            LineagesList.SelectedItem = catalog.Lineages.FirstOrDefault(
                    item => string.Equals(
                        item.Id,
                        selectStrategyId,
                        StringComparison.Ordinal))
                ?? catalog.Lineages.FirstOrDefault();
        }
        finally
        {
            _changingSelection = false;
        }
        if (LineagesList.SelectedItem is StrategyHistoryLineage lineage)
        {
            SelectLineage(lineage, selectVersion);
        }
        else
        {
            SelectLineage(null, null);
        }

        StatusText.Text = catalog.Errors.Count == 0
            ? $"Loaded {catalog.Lineages.Count} immutable custom-Strategy lineage(s)."
            : $"Loaded safe retained history with {catalog.Errors.Count} preserved error(s): "
                + string.Join(
                    "; ",
                    catalog.Errors.Select(item => $"{item.Id}: {item.Error}"));
        StatusText.Foreground = catalog.Errors.Count == 0
            ? (Brush)FindResource("MutedBrush")
            : new SolidColorBrush(Color.FromRgb(241, 191, 91));
    }

    private void LineagesList_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_changingSelection)
        {
            return;
        }
        SelectLineage(LineagesList.SelectedItem as StrategyHistoryLineage, null);
    }

    private void SelectLineage(
        StrategyHistoryLineage? lineage,
        int? selectVersion)
    {
        _lineage = lineage;
        _review = null;
        RestoreButton.IsEnabled = false;
        _changingSelection = true;
        try
        {
            RevisionsList.ItemsSource = lineage?.Revisions;
            RevisionsList.SelectedItem = lineage?.Revisions.FirstOrDefault(
                    item => item.LogicalVersion == selectVersion)
                ?? lineage?.Revisions.FirstOrDefault();
        }
        finally
        {
            _changingSelection = false;
        }
        LineageTitle.Text = lineage is null
            ? "Select a custom lineage"
            : $"{lineage.DisplayName} ({lineage.Id}) — {lineage.StatusLabel}";
        SelectRevision(RevisionsList.SelectedItem as StrategyRevisionSummary);
    }

    private void RevisionsList_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_changingSelection)
        {
            return;
        }
        SelectRevision(RevisionsList.SelectedItem as StrategyRevisionSummary);
    }

    private void SelectRevision(StrategyRevisionSummary? revision)
    {
        _revision = revision;
        _review = null;
        CompareButton.IsEnabled = !_busy && revision is not null;
        RestoreButton.IsEnabled = false;
        ReviewText.Text = revision is null
            ? "Select a revision to inspect its immutable metadata and fingerprints."
            : StrategyHistoryReviewFormatter.FormatRevision(revision);
    }

    private async void Compare_Click(object sender, RoutedEventArgs e)
    {
        if (_lineage is null || _revision is null)
        {
            return;
        }
        SetBusy(true, $"Comparing {_lineage.Id} version {_revision.LogicalVersion}...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(120));
            var operation = _revision.ActiveLatest
                ? "compare_strategy_revision"
                : "preview_restore_strategy";
            var response = await _api.PostStrategyAuthoringAsync(
                new
                {
                    operation,
                    strategy_id = _lineage.Id,
                    logical_version = _revision.LogicalVersion,
                    expected_revision_fingerprint = _revision.RevisionFingerprint,
                    expected_latest_source_fingerprint = _lineage.LatestSourceFingerprint,
                },
                cancellation.Token);
            _review = response;
            ReviewText.Text = StrategyHistoryReviewFormatter.FormatComparison(
                _revision,
                response);
            RestoreButton.IsEnabled = !_revision.ActiveLatest
                && response.Valid
                && response.Comparison?.Validation.Valid == true
                && !string.IsNullOrWhiteSpace(response.ReviewedRestoreFingerprint);
            StatusText.Text = _revision.ActiveLatest
                ? "Comparison complete. The selected revision is already current and cannot be restored."
                : RestoreButton.IsEnabled
                    ? "Restore review passed. Confirm explicitly to publish it as the next revision."
                    : "Comparison completed, but current validation did not permit restore.";
            StatusText.Foreground = RestoreButton.IsEnabled || _revision.ActiveLatest
                ? new SolidColorBrush(Color.FromRgb(101, 230, 166))
                : new SolidColorBrush(Color.FromRgb(241, 191, 91));
        }
        catch (Exception exc)
        {
            _review = null;
            RestoreButton.IsEnabled = false;
            ShowFailure(
                exc.Message
                + "\n\nNo revision was written. Any open Strategy draft remains unchanged; refresh history and review again after a conflict.");
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void Restore_Click(object sender, RoutedEventArgs e)
    {
        if (_lineage is null
            || _revision is null
            || _review is null
            || string.IsNullOrWhiteSpace(_review.ReviewedRestoreFingerprint))
        {
            return;
        }
        var confirmation =
            $"Publish {_lineage.DisplayName} historical version {_revision.LogicalVersion} "
            + $"as new latest version {_review.NextLogicalVersion}?\n\n"
            + "The retained historical revision will not be changed or moved. Linux will "
            + "rebuild its exact intent with current trusted code and append a new immutable revision.\n\n"
            + "After publication, the Windows client will select the Strategy. An active process queues its new latest definition for the next battle; a stopped process uses it for Start Automation without changing the saved startup default. "
            + "This will not switch the current battle, restart automation, Pause, or grant runtime input authority.";
        if (MessageBox.Show(
                this,
                confirmation,
                "Restore as new Strategy revision",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question,
                MessageBoxResult.No) != MessageBoxResult.Yes)
        {
            StatusText.Text = "Restore cancelled; no revision was written.";
            StatusText.Foreground = (Brush)FindResource("MutedBrush");
            return;
        }

        var strategyId = _lineage.Id;
        var selectedVersion = _revision.LogicalVersion;
        SetBusy(true, $"Publishing reviewed restore for {strategyId}...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(120));
            var response = await _api.PostStrategyAuthoringAsync(
                new
                {
                    operation = "publish_restore_strategy",
                    strategy_id = strategyId,
                    logical_version = selectedVersion,
                    expected_revision_fingerprint = _revision.RevisionFingerprint,
                    expected_latest_source_fingerprint = _lineage.LatestSourceFingerprint,
                    reviewed_restore_fingerprint = _review.ReviewedRestoreFingerprint,
                },
                cancellation.Token);
            if (!response.Restored || response.Profile is null)
            {
                throw new InvalidOperationException(
                    "Linux did not confirm an immutable restore-as-new publication.");
            }
            var restoredVersion = response.Profile.Version;
            await LoadHistoryAsync(strategyId, restoredVersion);
            StrategyPublicationUseResult? useResult = null;
            if (_publishedStrategyHandler is not null)
            {
                try
                {
                    useResult = await _publishedStrategyHandler(
                        new StrategyPublicationNotice(strategyId, restoredVersion));
                }
                catch (Exception exc)
                {
                    useResult = new StrategyPublicationUseResult(
                        false,
                        "The automatic next-boundary request failed after restore: "
                            + exc.Message);
                }
            }
            StrategyRestored?.Invoke(
                this,
                new StrategyRestoredEventArgs(
                    strategyId,
                    restoredVersion,
                    useResult?.Succeeded,
                    useResult?.Message));
            StatusText.Text =
                $"Restored historical version {selectedVersion} as new latest version "
                + $"{restoredVersion}. History and latest catalogs were refreshed; "
                + (useResult?.Message
                    ?? "the current battle was not switched.");
            StatusText.Foreground = useResult is { Succeeded: false }
                ? new SolidColorBrush(Color.FromRgb(241, 191, 91))
                : new SolidColorBrush(Color.FromRgb(101, 230, 166));
        }
        catch (Exception exc)
        {
            _review = null;
            RestoreButton.IsEnabled = false;
            ShowFailure(
                exc.Message
                + "\n\nNo unconfirmed retry was attempted. Any open Strategy draft remains unchanged; refresh history and review again.");
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) =>
        await LoadHistoryAsync(_lineage?.Id, _revision?.LogicalVersion);

    private void SetBusy(bool busy, string? message = null)
    {
        _busy = busy;
        LineagesList.IsEnabled = !busy;
        RevisionsList.IsEnabled = !busy;
        RefreshButton.IsEnabled = !busy;
        CompareButton.IsEnabled = !busy && _revision is not null;
        RestoreButton.IsEnabled = !busy
            && _review?.Valid == true
            && _review.Comparison?.Validation.Valid == true
            && _revision?.ActiveLatest == false
            && !string.IsNullOrWhiteSpace(_review.ReviewedRestoreFingerprint);
        if (!string.IsNullOrWhiteSpace(message))
        {
            StatusText.Text = message;
            StatusText.Foreground = (Brush)FindResource("MutedBrush");
        }
    }

    private void ShowFailure(string message)
    {
        StatusText.Text = message;
        StatusText.Foreground = new SolidColorBrush(Color.FromRgb(255, 124, 135));
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
