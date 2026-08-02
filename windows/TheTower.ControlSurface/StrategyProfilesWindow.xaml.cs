using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;

namespace TheTower.ControlSurface;

public partial class StrategyProfilesWindow : Window
{
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly ControlSurfaceApi _api;
    private readonly ObservableCollection<AuthoringSettingRowViewModel> _rows = [];
    private readonly ICollectionView _settingsView;
    private StrategyAuthoringCatalogResponse? _catalog;
    private StrategyAuthoringSource? _draftSource;
    private StrategyBaseItem? _selectedBase;
    private StrategyAuthoringStrategyItem? _selectedStrategy;
    private StrategyBaseUpdate? _baseUpdate;
    private StrategyBaseReference? _publishedBasePin;
    private string? _expectedFingerprint;
    private string? _reviewedRebaseFingerprint;
    private bool _isBase;
    private bool _isNew;
    private bool _busy;
    private bool _loading;
    private bool _changingSelection;

    public StrategyProfilesWindow(ControlSurfaceApi api)
    {
        InitializeComponent();
        _api = api;
        _settingsView = CollectionViewSource.GetDefaultView(_rows);
        _settingsView.GroupDescriptions.Add(
            new PropertyGroupDescription(nameof(AuthoringSettingRowViewModel.Section)));
        _settingsView.Filter = IncludeSetting;
        SettingsList.ItemsSource = _settingsView;
        EntityIdBox.TextChanged += DraftMetadata_Changed;
        DisplayNameBox.TextChanged += DraftMetadata_Changed;
        TierBox.TextChanged += DraftMetadata_Changed;
        Loaded += async (_, _) => await LoadCatalogAsync();
    }

    public string? PublishedStrategyId { get; private set; }

    private async Task LoadCatalogAsync(
        string? selectKind = null,
        string? selectId = null)
    {
        SetBusy(true, "Loading Base and Strategy catalogs...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(20));
            ApplyCatalog(
                await _api.GetStrategyAuthoringAsync(cancellation.Token),
                selectKind,
                selectId);
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

    private void ApplyCatalog(
        StrategyAuthoringCatalogResponse catalog,
        string? selectKind = null,
        string? selectId = null)
    {
        _catalog = catalog;
        _changingSelection = true;
        try
        {
            BasesList.ItemsSource = catalog.Bases.Items;
            StrategiesList.ItemsSource = catalog.Strategies.Items;
            BasesList.SelectedItem = null;
            StrategiesList.SelectedItem = null;
            if (string.Equals(selectKind, "base", StringComparison.Ordinal))
            {
                BasesList.SelectedItem = catalog.Bases.Items.FirstOrDefault(
                    item => string.Equals(item.Id, selectId, StringComparison.Ordinal));
            }
            else
            {
                StrategiesList.SelectedItem = catalog.Strategies.Items.FirstOrDefault(
                        item => string.Equals(item.Id, selectId, StringComparison.Ordinal))
                    ?? catalog.Strategies.Items.FirstOrDefault(item => item.Id == "farm_t18")
                    ?? catalog.Strategies.Items.FirstOrDefault();
            }
        }
        finally
        {
            _changingSelection = false;
        }

        if (BasesList.SelectedItem is StrategyBaseItem selectedBase)
        {
            SelectBase(selectedBase);
        }
        else if (StrategiesList.SelectedItem is StrategyAuthoringStrategyItem selectedStrategy)
        {
            SelectStrategy(selectedStrategy);
        }
        else
        {
            ClearEditor("Create a Base or Strategy to begin authoring.");
        }

        var errorCount = catalog.CatalogErrors.Count;
        StatusText.Text = errorCount == 0
            ? $"Loaded {catalog.Bases.Items.Count} Base(s) and "
                + $"{catalog.Strategies.Items.Count} Strategy item(s)."
            : $"Loaded the available catalog; {errorCount} invalid publication(s) "
                + "remain excluded and preserved for review: "
                + string.Join(
                    "; ",
                    catalog.CatalogErrors.Select(error =>
                        $"{error.Catalog}/{error.Id}: {error.Error}"));
        StatusText.Foreground = errorCount == 0
            ? (Brush)FindResource("MutedBrush")
            : new SolidColorBrush(Color.FromRgb(241, 191, 91));
    }

    private void BasesList_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_changingSelection
            || BasesList.SelectedItem is not StrategyBaseItem selected)
        {
            return;
        }
        _changingSelection = true;
        StrategiesList.SelectedItem = null;
        _changingSelection = false;
        SelectBase(selected);
    }

    private void StrategiesList_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_changingSelection
            || StrategiesList.SelectedItem is not StrategyAuthoringStrategyItem selected)
        {
            return;
        }
        _changingSelection = true;
        BasesList.SelectedItem = null;
        _changingSelection = false;
        SelectStrategy(selected);
    }

    private void SelectBase(StrategyBaseItem item)
    {
        _selectedBase = item;
        _selectedStrategy = null;
        _baseUpdate = null;
        _publishedBasePin = null;
        _isBase = true;
        _isNew = false;
        _expectedFingerprint = item.SourceFingerprint;
        _reviewedRebaseFingerprint = null;
        BeginSource(
            CloneSource(item.Source),
            resolution: item.Resolution,
            editable: item.Editable,
            help: "Editing this Base publishes a new immutable revision. Existing Strategies remain pinned to their embedded snapshots.");
        EditorTitle.Text = $"{item.DisplayName} — next revision {item.LatestRevision + 1}";
        ShowBaseUpdate(null, false);
        CloneButton.IsEnabled = false;
    }

    private void SelectStrategy(StrategyAuthoringStrategyItem item)
    {
        _selectedBase = null;
        _selectedStrategy = item;
        _baseUpdate = item.BaseUpdate;
        _publishedBasePin = item.Source?.Base is not null
            ? CloneBaseReference(item.Source.Base)
            : null;
        _isBase = false;
        _isNew = false;
        _expectedFingerprint = item.SourceFingerprint;
        _reviewedRebaseFingerprint = null;
        CloneButton.IsEnabled = item.AuthoringSupported && !_busy;

        if (!item.AuthoringSupported || item.Source is null)
        {
            ClearEditor(item.ReadOnlyReason ?? "This Strategy family is read-only.");
            EditorTitle.Text = item.DisplayName;
            EntityIdBox.Text = item.Id;
            DisplayNameBox.Text = item.DisplayName;
            return;
        }

        BeginSource(
            CloneSource(item.Source),
            item.Resolution,
            item.Editable,
            item.Editable
                ? item.LegacyConverted
                    ? "This schema-1 profile was conservatively converted in memory. Opening it changed no file; publication is the only migration boundary."
                    : "Local directives can be reset to inherited. Effective values and provenance come from the server resolver."
                : item.ReadOnlyReason
                    ?? "Bundled Strategies are immutable; clone this source to edit it.");
        EditorTitle.Text = item.DisplayName;
        ShowBaseUpdate(item.BaseUpdate, item.Editable);
    }

    private void BeginSource(
        StrategyAuthoringSource source,
        StrategyAuthoringResolution? resolution,
        bool editable,
        string help)
    {
        _loading = true;
        try
        {
            _draftSource = source;
            EditorPanel.IsEnabled = true;
            EntityIdBox.Text = source.Id;
            DisplayNameBox.Text = source.DisplayName;
            TierBox.Text = source.Tier?.ToString(CultureInfo.InvariantCulture) ?? "";
            EntityIdBox.IsEnabled = editable && _isNew;
            DisplayNameBox.IsEnabled = editable;
            TierBox.IsEnabled = editable && !_isBase;
            ConfigureBasePin(source, editable);
            PopulateRows(source, resolution, editable);
            EditorHelpText.Text = help;
            ValidateButton.IsEnabled = editable && !_busy;
            PublishButton.IsEnabled = editable && !_busy;
            NewBaseButton.IsEnabled = !_busy;
            NewStrategyButton.IsEnabled = !_busy;
            ValidationSummaryText.Text = editable
                ? "Validate to refresh effective values, provenance, fingerprints, and generated rule count."
                : "Read-only source. Clone a supported Strategy to create an editable draft.";
            ValidationSummaryText.Foreground = (Brush)FindResource("MutedBrush");
        }
        finally
        {
            _loading = false;
        }
    }

    private void PopulateRows(
        StrategyAuthoringSource source,
        StrategyAuthoringResolution? resolution,
        bool editable,
        IReadOnlyDictionary<string, AuthoringDormantValue>? dormantValues = null)
    {
        foreach (var row in _rows)
        {
            row.PropertyChanged -= Row_PropertyChanged;
        }
        _rows.Clear();
        if (_catalog is null)
        {
            return;
        }
        foreach (var definition in _catalog.SettingRegistry)
        {
            source.Settings.TryGetValue(definition.Id, out var directive);
            StrategyResolvedSetting? resolved = null;
            if (resolution is not null)
            {
                resolution.Settings.TryGetValue(definition.Id, out resolved);
            }
            var row = new AuthoringSettingRowViewModel(
                definition,
                _isBase,
                editable,
                directive,
                resolved,
                _catalog.Capabilities,
                dormantValues is not null
                    && dormantValues.TryGetValue(definition.Id, out var dormant)
                        ? dormant
                        : null);
            row.PropertyChanged += Row_PropertyChanged;
            _rows.Add(row);
        }
        _settingsView.Refresh();
    }

    private void ConfigureBasePin(
        StrategyAuthoringSource source,
        bool editable)
    {
        if (_isBase)
        {
            BasePinPanel.Visibility = Visibility.Collapsed;
            BasePinBox.ItemsSource = null;
            return;
        }

        var choices = new List<StrategyBasePinChoice>
        {
            new()
            {
                DisplayName = "No Base — unmanaged settings stay unmanaged",
            },
        };
        if (source.Base is not null)
        {
            choices.Add(
                new StrategyBasePinChoice
                {
                    DisplayName = $"{source.Base.Id} revision {source.Base.Revision}",
                    Reference = CloneBaseReference(source.Base),
                });
        }
        foreach (var compatible in (_catalog?.LatestCompatibleBaseRevisions ?? [])
                     .Where(item => string.Equals(
                         item.Family,
                         source.Family,
                         StringComparison.Ordinal)))
        {
            if (choices.Any(choice =>
                    choice.Reference?.Id == compatible.Id
                    && choice.Reference.Revision == compatible.Revision))
            {
                continue;
            }
            choices.Add(
                new StrategyBasePinChoice
                {
                    DisplayName = $"{compatible.DisplayName} revision {compatible.Revision} (latest)",
                    Reference = new StrategyBaseReference
                    {
                        Id = compatible.Id,
                        Revision = compatible.Revision,
                    },
                });
        }

        BasePinBox.ItemsSource = choices;
        BasePinBox.SelectedItem = choices.First(choice =>
            source.Base is null
                ? choice.Reference is null
                : choice.Reference?.Id == source.Base.Id
                    && choice.Reference.Revision == source.Base.Revision);
        var canChooseFirstBase = !_isNew && _publishedBasePin is null;
        BasePinBox.IsEnabled = editable && (_isNew || canChooseFirstBase);
        BasePinHelpText.Text = _isNew
            ? "A new Strategy may pin a latest compatible Base. Reset local directives to inherit from it."
            : canChooseFirstBase
                ? "Choose the first compatible Base, then review its semantic changes before publishing. The Strategy ID and activation remain unchanged."
                : "Published Base pins change only through the explicit reviewed update workflow.";
        BasePinPanel.Visibility = Visibility.Visible;
    }

    private void ClearEditor(string help)
    {
        _loading = true;
        try
        {
            _draftSource = null;
            _rows.Clear();
            EditorPanel.IsEnabled = false;
            EntityIdBox.Text = "";
            DisplayNameBox.Text = "";
            TierBox.Text = "";
            BasePinPanel.Visibility = Visibility.Collapsed;
            BasePinBox.ItemsSource = null;
            EditorHelpText.Text = help;
            RebaseBanner.Visibility = Visibility.Collapsed;
            ValidateButton.IsEnabled = false;
            PublishButton.IsEnabled = false;
            ValidationSummaryText.Text = help;
            CloneButton.IsEnabled = false;
        }
        finally
        {
            _loading = false;
        }
    }

    private void NewBase_Click(object sender, RoutedEventArgs e)
    {
        _selectedBase = null;
        _selectedStrategy = null;
        _isBase = true;
        _isNew = true;
        _expectedFingerprint = null;
        _reviewedRebaseFingerprint = null;
        _baseUpdate = null;
        _publishedBasePin = null;
        BeginSource(
            new StrategyAuthoringSource
            {
                Kind = "base",
                Id = SuggestedId("farm_base", _catalog?.Bases.Items.Select(item => item.Id)),
                DisplayName = "Farm Base",
                Family = "farm",
                Revision = 1,
                Settings = [],
            },
            resolution: null,
            editable: true,
            help: "A Base is sparse and never activatable. Include only settings this reusable Base should own.");
        EditorTitle.Text = "New Base";
        ShowBaseUpdate(null, false);
        EntityIdBox.Focus();
        EntityIdBox.SelectAll();
    }

    private void NewStrategy_Click(object sender, RoutedEventArgs e)
    {
        var template = _catalog?.Strategies.Items.FirstOrDefault(
            item => item.Id == "farm_t18" && item.Source is not null);
        if (template?.Source is null)
        {
            ShowFailure("The bundled Farm T18 authoring source is unavailable.");
            return;
        }
        BeginStrategyClone(template);
    }

    private void CloneStrategy_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedStrategy is { AuthoringSupported: true, Source: not null } template)
        {
            BeginStrategyClone(template);
        }
    }

    private void BeginStrategyClone(StrategyAuthoringStrategyItem template)
    {
        var source = CloneSource(template.Source!);
        var ids = _catalog?.Strategies.Items.Select(item => item.Id);
        source.Id = SuggestedId($"{template.Id}_custom", ids);
        source.DisplayName = $"{template.DisplayName} Custom";
        source.Version = 1;
        _selectedBase = null;
        _selectedStrategy = null;
        _isBase = false;
        _isNew = true;
        _expectedFingerprint = null;
        _reviewedRebaseFingerprint = null;
        _baseUpdate = null;
        _publishedBasePin = null;
        BeginSource(
            source,
            template.Resolution,
            editable: true,
            help: "Cloned source starts as explicit local intent. Reset individual directives to inherited when a pinned Base supplies them.");
        EditorTitle.Text = "New Strategy draft";
        ShowBaseUpdate(null, false);
        CloneButton.IsEnabled = false;
        EntityIdBox.Focus();
        EntityIdBox.SelectAll();
    }

    private async void Validate_Click(object sender, RoutedEventArgs e) =>
        await ValidateDraftAsync(showSuccess: true);

    private async Task<StrategyAuthoringMutationResponse?> ValidateDraftAsync(
        bool showSuccess)
    {
        StrategyAuthoringSource source;
        try
        {
            source = BuildDraftSource();
        }
        catch (Exception exc)
        {
            ShowFailure(exc.Message);
            return null;
        }

        SetBusy(true, _isBase ? "Validating Base draft..." : "Resolving Strategy draft...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(120));
            var response = await _api.PostStrategyAuthoringAsync(
                new
                {
                    operation = _isBase ? "validate_base" : "validate_strategy",
                    source,
                },
                cancellation.Token);
            ApplyValidatedDraft(response);
            if (showSuccess)
            {
                StatusText.Text = _isBase
                    ? $"Base draft is valid for immutable revision {response.Source.Revision}; no file was changed."
                    : $"Strategy draft is valid and resolves to {response.RuleCount} generated rules; no file was changed.";
                StatusText.Foreground = new SolidColorBrush(Color.FromRgb(101, 230, 166));
            }
            return response;
        }
        catch (Exception exc)
        {
            ShowFailureWithConflictContext(exc.Message);
            return null;
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void Publish_Click(object sender, RoutedEventArgs e)
    {
        if (RequiresReviewedBaseSelection())
        {
            ShowFailure(
                "Review the selected Base before publishing. The server must show and approve its effective changes first.");
            return;
        }
        var review = await ValidateDraftAsync(showSuccess: false);
        if (review is null)
        {
            return;
        }
        var reviewText = StrategyAuthoringReviewFormatter.FormatPublishReview(
            review,
            _isBase);
        if (MessageBox.Show(
                this,
                reviewText,
                _isBase ? "Review Base publication" : "Review Strategy publication",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question) != MessageBoxResult.Yes)
        {
            StatusText.Text = "Publication cancelled; the validated draft remains open.";
            StatusText.Foreground = (Brush)FindResource("MutedBrush");
            return;
        }

        SetBusy(true, _isBase ? "Publishing immutable Base revision..." : "Publishing Strategy...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(120));
            var response = await _api.PostStrategyAuthoringAsync(
                new
                {
                    operation = _isBase ? "publish_base" : "publish_strategy",
                    source = BuildDraftSource(),
                    expected_latest_fingerprint = _isBase
                        ? _expectedFingerprint
                        : null,
                    expected_source_fingerprint = _isBase
                        ? null
                        : _expectedFingerprint,
                    reviewed_rebase_fingerprint = _isBase
                        ? null
                        : _reviewedRebaseFingerprint,
                },
                cancellation.Token);

            var publishedKind = _isBase ? "base" : "strategy";
            string successMessage;
            if (_isBase)
            {
                successMessage = $"Published Base {response.Source.DisplayName} revision "
                    + $"{response.Source.Revision}. Existing Strategies remain pinned.";
            }
            else
            {
                PublishedStrategyId = response.Profile?.Id ?? response.Source.Id;
                successMessage = $"Published Strategy {response.Source.DisplayName} version "
                    + $"{response.Profile?.Version}. It was not activated.";
            }
            if (!string.IsNullOrWhiteSpace(response.Warning))
            {
                successMessage += $" Audit warning: {response.Warning}";
            }
            _reviewedRebaseFingerprint = null;
            if (response.Catalog is not null)
            {
                ApplyCatalog(
                    response.Catalog,
                    publishedKind,
                    response.Source.Id);
            }
            else
            {
                await LoadCatalogAsync(
                    publishedKind,
                    response.Source.Id);
            }
            StatusText.Text = successMessage;
            StatusText.Foreground = new SolidColorBrush(Color.FromRgb(101, 230, 166));
        }
        catch (Exception exc)
        {
            ShowFailureWithConflictContext(exc.Message);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void ReviewRebase_Click(object sender, RoutedEventArgs e)
    {
        var targetBase = BaseReviewTarget();
        if (targetBase is null || _isBase)
        {
            return;
        }
        var attachingFirstBase = _publishedBasePin is null;
        StrategyAuthoringSource source;
        try
        {
            source = BuildDraftSource();
            source.Base = _publishedBasePin is null
                ? null
                : CloneBaseReference(_publishedBasePin);
        }
        catch (Exception exc)
        {
            ShowFailure(exc.Message);
            return;
        }

        SetBusy(
            true,
            attachingFirstBase
                ? "Computing reviewed Base attachment on Linux..."
                : "Computing reviewed Base rebase on Linux...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(120));
            var response = await _api.PostStrategyAuthoringAsync(
                new
                {
                    operation = "preview_rebase",
                    source,
                    target_base = new
                    {
                        id = targetBase.Id,
                        revision = targetBase.Revision,
                    },
                },
                cancellation.Token);
            var reviewText = StrategyAuthoringReviewFormatter.FormatRebaseReview(response);
            if (!response.Valid)
            {
                MessageBox.Show(
                    this,
                    reviewText,
                    attachingFirstBase
                        ? "Base attachment validation failed"
                        : "Rebase validation failed",
                    MessageBoxButton.OK,
                    MessageBoxImage.Warning);
                StatusText.Text = "The Base review found validation errors; the draft pin was not changed.";
                StatusText.Foreground = new SolidColorBrush(Color.FromRgb(255, 113, 135));
                return;
            }
            if (MessageBox.Show(
                    this,
                    reviewText,
                    attachingFirstBase
                        ? "Review Base attachment"
                        : "Review Base rebase",
                    MessageBoxButton.YesNo,
                    MessageBoxImage.Question) != MessageBoxResult.Yes)
            {
                StatusText.Text = "Base review cancelled; the draft pin was not changed.";
                StatusText.Foreground = (Brush)FindResource("MutedBrush");
                return;
            }

            _reviewedRebaseFingerprint = response.ReviewedRebaseFingerprint;
            var dormantValues = CaptureDormantValues();
            _draftSource = CloneSource(response.Source);
            _loading = true;
            try
            {
                ConfigureBasePin(_draftSource, editable: true);
                PopulateRows(
                    _draftSource,
                    response.Resolution,
                    editable: true,
                    dormantValues);
                RebaseBanner.Visibility = Visibility.Collapsed;
                ValidationSummaryText.Text =
                    $"Reviewed Base {(attachingFirstBase ? "attachment" : "rebase")} accepted for {_draftSource.Base?.Id} revision "
                    + $"{_draftSource.Base?.Revision}. Validate and Review & Publish to persist it.";
                ValidationSummaryText.Foreground = new SolidColorBrush(
                    Color.FromRgb(101, 230, 166));
            }
            finally
            {
                _loading = false;
            }
            StatusText.Text = "The draft pin changed after review; no publication or activation occurred.";
            StatusText.Foreground = new SolidColorBrush(Color.FromRgb(101, 230, 166));
        }
        catch (Exception exc)
        {
            ShowFailureWithConflictContext(exc.Message);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void ApplyValidatedDraft(StrategyAuthoringMutationResponse response)
    {
        var dormantValues = CaptureDormantValues();
        _draftSource = CloneSource(response.Source);
        _loading = true;
        try
        {
            ConfigureBasePin(_draftSource, editable: true);
            PopulateRows(
                _draftSource,
                response.Resolution,
                editable: true,
                dormantValues);
            var summary = response.Summary.Count > 0
                ? string.Join(Environment.NewLine, response.Summary)
                : "Validation passed.";
            if (response.Fingerprints.Count > 0)
            {
                summary += Environment.NewLine + string.Join(
                    Environment.NewLine,
                    response.Fingerprints.OrderBy(item => item.Key).Select(item =>
                        $"{item.Key}: {item.Value}"));
            }
            ValidationSummaryText.Text = summary;
            ValidationSummaryText.Foreground = new SolidColorBrush(
                Color.FromRgb(101, 230, 166));
        }
        finally
        {
            _loading = false;
        }
    }

    private StrategyAuthoringSource BuildDraftSource()
    {
        if (_draftSource is null)
        {
            throw new InvalidOperationException("Select or create an authoring document first.");
        }
        var source = CloneSource(_draftSource);
        source.Id = EntityIdBox.Text.Trim();
        source.DisplayName = DisplayNameBox.Text.Trim();
        source.Settings = [];
        foreach (var row in _rows)
        {
            var directive = row.BuildDirective();
            if (directive is not null)
            {
                source.Settings[row.Id] = directive;
            }
        }
        if (_isBase)
        {
            source.Kind = "base";
            source.Tier = null;
            source.Version = null;
            source.Base = null;
            source.Revision ??= 1;
        }
        else
        {
            source.Kind = "strategy";
            source.Revision = null;
            source.Base = BasePinBox.SelectedItem is StrategyBasePinChoice
                {
                    Reference: not null,
                } choice
                ? CloneBaseReference(choice.Reference)
                : null;
            if (!int.TryParse(
                    TierBox.Text.Trim(),
                    NumberStyles.None,
                    CultureInfo.InvariantCulture,
                    out var tier))
            {
                throw new InvalidOperationException("Strategy Tier must be an integer.");
            }
            source.Tier = tier;
            source.Version ??= 1;
        }
        return source;
    }

    private Dictionary<string, AuthoringDormantValue> CaptureDormantValues() =>
        _rows.ToDictionary(
            row => row.Id,
            row => row.CaptureDormantValue(),
            StringComparer.Ordinal);

    private StrategyBaseReference? SelectedBasePin() =>
        BasePinBox.SelectedItem is StrategyBasePinChoice
        {
            Reference: not null,
        } choice
            ? CloneBaseReference(choice.Reference)
            : null;

    private StrategyBaseReference? BaseReviewTarget()
    {
        if (_baseUpdate is not null)
        {
            return new StrategyBaseReference
            {
                Id = _baseUpdate.Id,
                Revision = _baseUpdate.LatestRevision,
            };
        }
        if (_isBase || _isNew || _publishedBasePin is not null)
        {
            return null;
        }
        return SelectedBasePin();
    }

    private bool RequiresReviewedBaseSelection() =>
        !_isBase
        && !_isNew
        && !SameBaseReference(_publishedBasePin, SelectedBasePin())
        && _reviewedRebaseFingerprint is null;

    private static bool SameBaseReference(
        StrategyBaseReference? first,
        StrategyBaseReference? second) =>
        first?.Id == second?.Id
        && first?.Revision == second?.Revision;

    private void ShowBaseUpdate(StrategyBaseUpdate? update, bool editable)
    {
        _baseUpdate = update;
        if (update is not null)
        {
            RebaseBannerText.Text = $"{update.DisplayName} revision {update.LatestRevision} is available; "
                + $"the published Strategy remains pinned to revision {update.PinnedRevision}.";
            ReviewRebaseButton.Content = "Review Base update...";
            ReviewRebaseButton.IsEnabled = editable && !_busy;
            RebaseBanner.Visibility = Visibility.Visible;
            return;
        }

        var target = BaseReviewTarget();
        if (target is null)
        {
            RebaseBanner.Visibility = Visibility.Collapsed;
            return;
        }
        var selectedLabel = BasePinBox.SelectedItem is StrategyBasePinChoice choice
            ? choice.DisplayName
            : $"{target.Id} revision {target.Revision}";
        RebaseBannerText.Text = $"{selectedLabel} will become this Strategy's first Base. "
            + "Review inherited values and provenance before publishing.";
        ReviewRebaseButton.Content = "Review Base selection...";
        ReviewRebaseButton.IsEnabled = editable && !_busy;
        RebaseBanner.Visibility = Visibility.Visible;
    }

    private bool IncludeSetting(object item) =>
        ShowAllSettingsButton.IsChecked == true
        || item is AuthoringSettingRowViewModel { IsActive: true };

    private void SettingFilter_Changed(object sender, RoutedEventArgs e)
    {
        if (IsInitialized)
        {
            _settingsView.Refresh();
        }
    }

    private void Row_PropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (_loading)
        {
            return;
        }
        if (e.PropertyName is nameof(AuthoringSettingRowViewModel.Dirty)
            or nameof(AuthoringSettingRowViewModel.SelectedSourceState))
        {
            InvalidateReviewedRebase();
            if (ShowActiveOnlyButton.IsChecked == true)
            {
                _settingsView.Refresh();
            }
        }
    }

    private void DraftMetadata_Changed(object sender, TextChangedEventArgs e)
    {
        if (!_loading)
        {
            InvalidateReviewedRebase();
        }
    }

    private void BasePin_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_loading)
        {
            return;
        }
        InvalidateReviewedRebase();
        ValidationSummaryText.Text =
            BaseReviewTarget() is null || _isNew
                ? "The draft Base selection changed. Validate to refresh inherited values and provenance."
                : "The draft Base selection changed. Review the Base selection to refresh inherited values and provenance before publishing.";
        ValidationSummaryText.Foreground = new SolidColorBrush(
            Color.FromRgb(241, 191, 91));
        RefreshAuthoringActionButtons();
    }

    private void InvalidateReviewedRebase()
    {
        var reviewInvalidated = _reviewedRebaseFingerprint is not null;
        _reviewedRebaseFingerprint = null;
        ShowBaseUpdate(_baseUpdate, editable: true);
        RefreshAuthoringActionButtons();
        if (!reviewInvalidated)
        {
            return;
        }
        ValidationSummaryText.Text =
            "The draft changed after its Base review. Review the Base selection again before publishing.";
        ValidationSummaryText.Foreground = new SolidColorBrush(
            Color.FromRgb(241, 191, 91));
    }

    private void ResetToInherited_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is AuthoringSettingRowViewModel row)
        {
            row.ResetToInherited();
        }
    }

    private void AddListItem_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is AuthoringSettingRowViewModel row)
        {
            row.AddSelectedListItem();
        }
    }

    private void RemoveListItem_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as Button) is { DataContext: AuthoringSettingRowViewModel row } button)
        {
            row.RemoveListItem(button.CommandParameter as StrategyEditorOption);
        }
    }

    private void MoveListItemUp_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as Button) is { DataContext: AuthoringSettingRowViewModel row } button)
        {
            row.MoveListItem(button.CommandParameter as StrategyEditorOption, -1);
        }
    }

    private void MoveListItemDown_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as Button) is { DataContext: AuthoringSettingRowViewModel row } button)
        {
            row.MoveListItem(button.CommandParameter as StrategyEditorOption, 1);
        }
    }

    private void RefreshAuthoringActionButtons()
    {
        var canEdit = !_busy
            && _draftSource is not null
            && (_isNew
                || _selectedBase?.Editable == true
                || _selectedStrategy?.Editable == true);
        ValidateButton.IsEnabled = canEdit;
        PublishButton.IsEnabled = canEdit && !RequiresReviewedBaseSelection();
        ReviewRebaseButton.IsEnabled = !_busy
            && BaseReviewTarget() is not null
            && (_isNew || _selectedStrategy?.Editable == true);
    }

    private void SetBusy(bool busy, string? message = null)
    {
        _busy = busy;
        BasesList.IsEnabled = !busy;
        StrategiesList.IsEnabled = !busy;
        NewBaseButton.IsEnabled = !busy;
        NewStrategyButton.IsEnabled = !busy;
        CloneButton.IsEnabled = !busy
            && _selectedStrategy is { AuthoringSupported: true };
        RefreshAuthoringActionButtons();
        if (!string.IsNullOrWhiteSpace(message))
        {
            StatusText.Text = message;
            StatusText.Foreground = (Brush)FindResource("MutedBrush");
        }
    }

    private void ShowFailureWithConflictContext(string message)
    {
        var conflict = message.Contains("changed after it was opened", StringComparison.OrdinalIgnoreCase)
            || message.Contains("reload", StringComparison.OrdinalIgnoreCase);
        ShowFailure(
            conflict
                ? message + "\n\nThe draft remains open. Reload the catalog and reconcile it before publishing."
                : message);
    }

    private void ShowFailure(string message)
    {
        StatusText.Text = message;
        StatusText.Foreground = new SolidColorBrush(Color.FromRgb(255, 113, 135));
        ValidationSummaryText.Text = "Validation or publication failed; no activation occurred.";
        ValidationSummaryText.Foreground = new SolidColorBrush(
            Color.FromRgb(255, 113, 135));
    }

    private static StrategyAuthoringSource CloneSource(StrategyAuthoringSource source) =>
        JsonSerializer.Deserialize<StrategyAuthoringSource>(
            JsonSerializer.Serialize(source, Json),
            Json)
        ?? throw new InvalidOperationException("Unable to clone authoring source.");

    private static StrategyBaseReference CloneBaseReference(
        StrategyBaseReference source) => new()
        {
            Id = source.Id,
            Revision = source.Revision,
        };

    private static string SuggestedId(
        string preferred,
        IEnumerable<string>? existingIds)
    {
        var existing = (existingIds ?? []).ToHashSet(
            StringComparer.OrdinalIgnoreCase);
        var baseId = preferred.Length <= 48
            ? preferred
            : preferred[..48].TrimEnd('_');
        var candidate = baseId;
        for (var suffix = 2; existing.Contains(candidate); suffix++)
        {
            var suffixText = $"_{suffix}";
            candidate = baseId[..Math.Min(baseId.Length, 48 - suffixText.Length)]
                + suffixText;
        }
        return candidate;
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
