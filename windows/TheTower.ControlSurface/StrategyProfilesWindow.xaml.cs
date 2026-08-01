using System.Collections.ObjectModel;
using System.Globalization;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace TheTower.ControlSurface;

public partial class StrategyProfilesWindow : Window
{
    private readonly ControlSurfaceApi _api;
    private StrategyProfileCatalogResponse? _catalog;
    private StrategyProfileItem? _selectedProfile;
    private string? _expectedSourceFingerprint;
    private bool _busy;
    private bool _draftIsNew;
    private Dictionary<string, JsonElement> _setupSettings = [];
    private readonly ObservableCollection<StrategyPresetOption> _perkBans = [];
    private readonly ObservableCollection<StrategyPresetOption> _autoPickOrder = [];

    public StrategyProfilesWindow(ControlSurfaceApi api)
    {
        InitializeComponent();
        _api = api;
        PerkBansList.ItemsSource = _perkBans;
        AutoPickOrderList.ItemsSource = _autoPickOrder;
        Loaded += async (_, _) => await LoadCatalogAsync();
    }

    public string? PublishedStrategyId { get; private set; }

    private async Task LoadCatalogAsync(string? selectId = null)
    {
        SetBusy(true, "Loading strategy profile catalog...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(20));
            ApplyCatalog(
                await _api.GetStrategyProfilesAsync(cancellation.Token),
                selectId);
        }
        catch (Exception exc)
        {
            StatusText.Text = exc.Message;
            StatusText.Foreground = new SolidColorBrush(
                Color.FromRgb(255, 113, 135));
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void ApplyCatalog(
        StrategyProfileCatalogResponse catalog,
        string? selectId = null)
    {
        _catalog = catalog;
        SetModeItems(ModulesModeBox, catalog.PolicyModes);
        SetModeItems(DamageModeBox, catalog.PolicyModes);
        SetModeItems(OrbModeBox, catalog.PolicyModes);
        SetModeItems(TargetModeBox, catalog.PolicyModes);
        ModulesPresetBox.ItemsSource = Presets("modules");
        OrbPresetBox.ItemsSource = Presets("orb_distance");
        TargetPresetBox.ItemsSource = Presets("target_priority");
        ProfilesList.ItemsSource = catalog.Items;

        var selected = catalog.Items.FirstOrDefault(profile => string.Equals(
                profile.Id,
                selectId,
                StringComparison.OrdinalIgnoreCase))
            ?? catalog.Items.FirstOrDefault(profile => profile.Id == "farm_t18")
            ?? catalog.Items.FirstOrDefault();
        ProfilesList.SelectedItem = selected;
        if (selected is not null)
        {
            ProfilesList.ScrollIntoView(selected);
        }

        StatusText.Text = catalog.Errors.Count == 0
            ? $"Loaded {catalog.Items.Count} selectable profiles."
            : $"Loaded {catalog.Items.Count} profiles; "
                + $"{catalog.Errors.Count} invalid custom publication(s) were excluded: "
                + string.Join(
                    "; ",
                    catalog.Errors.Select(error => $"{error.Id}: {error.Error}"));
        StatusText.Foreground = catalog.Errors.Count == 0
            ? (Brush)FindResource("MutedBrush")
            : new SolidColorBrush(Color.FromRgb(241, 191, 91));
    }

    private List<StrategyPresetOption> Presets(string setting) =>
        _catalog?.Presets.TryGetValue(setting, out var values) == true
            ? values
            : [];

    private static void SetModeItems(
        ComboBox box,
        IEnumerable<string> modes)
    {
        box.ItemsSource = modes.ToArray();
    }

    private void ProfilesList_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (ProfilesList.SelectedItem is not StrategyProfileItem profile)
        {
            return;
        }
        _selectedProfile = profile;
        _draftIsNew = false;
        _expectedSourceFingerprint = profile.SourceFingerprint;
        CloneButton.IsEnabled = IsFarmProfile(profile) && !_busy;

        if (!IsFarmProfile(profile)
            || profile.Loadout is null
            || profile.Setup is null)
        {
            EditorTitle.Text = profile.DisplayName;
            EditorHelpText.Text = profile.Id == "tournament"
                ? "Tournament is generated from its dedicated observer profile and is not editable in the Farm Profile Builder."
                : "No Strategy has no generated plan to edit.";
            ClearEditor();
            SetEditorEnabled(false);
            return;
        }

        PopulateEditor(profile);
        SetEditorEnabled(profile.Editable);
        EditorTitle.Text = profile.DisplayName;
        EditorHelpText.Text = profile.Editable
            ? "This custom profile can be validated and republished. Its ID is immutable; clone it to create a renamed variant."
            : "Bundled profiles are read-only. Clone this profile to create an editable custom version.";
        ValidationSummaryText.Text = profile.Editable
            ? $"Published version {profile.Version}. Validate changes before publishing a new version."
            : $"Bundled version {profile.Version}. Clone it to begin a custom draft.";
    }

    private void PopulateEditor(StrategyProfileItem profile)
    {
        var loadout = profile.Loadout
            ?? throw new InvalidOperationException("Farm profile has no loadout.");
        var setup = profile.Setup
            ?? throw new InvalidOperationException("Farm profile has no setup.");
        ProfileIdBox.Text = profile.Id;
        DisplayNameBox.Text = profile.DisplayName;
        TierBox.Text = profile.Tier?.ToString(CultureInfo.InvariantCulture) ?? "";
        SetPolicy(
            ModulesModeBox,
            ModulesPresetBox,
            loadout.Modules);
        DamageModeBox.SelectedItem = loadout.DamageSlider.Mode;
        DamageValueBox.Text = loadout.DamageSlider.Value ?? "";
        SetPolicy(
            OrbModeBox,
            OrbPresetBox,
            loadout.OrbDistance);
        SetPolicy(
            TargetModeBox,
            TargetPresetBox,
            loadout.TargetPriority);
        _setupSettings = setup.Settings.ToDictionary(
            pair => pair.Key,
            pair => pair.Value.Clone(),
            StringComparer.Ordinal);
        SetSkippedChecks(setup.SkippedChecks);
        ReplacePerks(
            _perkBans,
            SetupSettingList(setup, "perk_bans"));
        ReplacePerks(
            _autoPickOrder,
            SetupSettingList(setup, "perk_auto_pick_order"));
        RefreshAvailablePerks();
        UpdatePolicyInputs();
        UpdateSetupInputs();
    }

    private static void SetPolicy(
        ComboBox modeBox,
        ComboBox presetBox,
        StrategyProfilePolicy policy)
    {
        modeBox.SelectedItem = policy.Mode;
        presetBox.SelectedValue = policy.Preset;
    }

    private static List<string> SetupSettingList(
        StrategyProfileSetup setup,
        string setting)
    {
        if (!setup.Settings.TryGetValue(setting, out var value)
            || value.ValueKind != JsonValueKind.Array)
        {
            throw new InvalidOperationException(
                $"Farm profile setup is missing {setting}.");
        }
        return value.EnumerateArray()
            .Select(item => item.GetString()?.Trim() ?? "")
            .Where(item => item.Length > 0)
            .ToList();
    }

    private void ReplacePerks(
        ObservableCollection<StrategyPresetOption> destination,
        IEnumerable<string> identifiers)
    {
        var catalog = (_catalog?.Perks ?? []).ToDictionary(
            option => option.Id,
            StringComparer.Ordinal);
        destination.Clear();
        foreach (var identifier in identifiers)
        {
            if (!catalog.TryGetValue(identifier, out var option))
            {
                throw new InvalidOperationException(
                    $"Profile references unsupported perk {identifier}.");
            }
            destination.Add(option);
        }
    }

    private void SetSkippedChecks(IEnumerable<string> skippedChecks)
    {
        var skipped = skippedChecks.ToHashSet(StringComparer.Ordinal);
        SkipAutoPickEnabledBox.IsChecked = skipped.Contains("auto_pick_perks");
        SkipPerkBansBox.IsChecked = skipped.Contains("perk_bans");
        SkipAutoPickOrderBox.IsChecked = skipped.Contains("perk_auto_pick_order");
    }

    private List<string> SkippedChecks()
    {
        var selected = new List<string>();
        if (SkipAutoPickEnabledBox.IsChecked == true)
        {
            selected.Add("auto_pick_perks");
        }
        if (SkipPerkBansBox.IsChecked == true)
        {
            selected.Add("perk_bans");
        }
        if (SkipAutoPickOrderBox.IsChecked == true)
        {
            selected.Add("perk_auto_pick_order");
        }
        return selected;
    }

    private void RefreshAvailablePerks()
    {
        var perks = _catalog?.Perks ?? [];
        var banned = _perkBans.Select(option => option.Id).ToHashSet(
            StringComparer.Ordinal);
        var ranked = _autoPickOrder.Select(option => option.Id).ToHashSet(
            StringComparer.Ordinal);
        AvailableBanPerkBox.ItemsSource = perks
            .Where(option => !banned.Contains(option.Id))
            .ToList();
        AvailableAutoPickPerkBox.ItemsSource = perks
            .Where(option => !ranked.Contains(option.Id))
            .ToList();
        AvailableBanPerkBox.SelectedIndex =
            AvailableBanPerkBox.Items.Count > 0 ? 0 : -1;
        AvailableAutoPickPerkBox.SelectedIndex =
            AvailableAutoPickPerkBox.Items.Count > 0 ? 0 : -1;
        UpdatePerkButtons();
    }

    private void ClearEditor()
    {
        ProfileIdBox.Text = "";
        DisplayNameBox.Text = "";
        TierBox.Text = "";
        ModulesModeBox.SelectedItem = null;
        DamageModeBox.SelectedItem = null;
        OrbModeBox.SelectedItem = null;
        TargetModeBox.SelectedItem = null;
        ModulesPresetBox.SelectedItem = null;
        OrbPresetBox.SelectedItem = null;
        TargetPresetBox.SelectedItem = null;
        DamageValueBox.Text = "";
        _setupSettings = [];
        _perkBans.Clear();
        _autoPickOrder.Clear();
        SetSkippedChecks([]);
        RefreshAvailablePerks();
        ValidationSummaryText.Text =
            "This profile type is outside the constrained Farm editor.";
    }

    private void SetEditorEnabled(bool enabled)
    {
        EditorPanel.IsEnabled = enabled;
        ProfileIdBox.IsEnabled = enabled && _draftIsNew;
        ValidateButton.IsEnabled = enabled && !_busy;
        PublishButton.IsEnabled = enabled && !_busy;
        UpdateSetupInputs();
    }

    private void NewProfile_Click(object sender, RoutedEventArgs e)
    {
        var template = _catalog?.Items.FirstOrDefault(profile =>
            profile.Id == "farm_t18");
        if (template is null || template.Loadout is null || template.Setup is null)
        {
            StatusText.Text = "The bundled Farm T18 template is unavailable.";
            return;
        }
        BeginNewDraft(template, SuggestedId(template.Tier ?? 18));
    }

    private void CloneProfile_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedProfile is not { Loadout: not null } profile
            || !IsFarmProfile(profile))
        {
            return;
        }
        BeginNewDraft(profile, SuggestedId(profile.Tier ?? 18));
    }

    private void BeginNewDraft(StrategyProfileItem template, string identifier)
    {
        ProfilesList.SelectedItem = null;
        _selectedProfile = null;
        _draftIsNew = true;
        _expectedSourceFingerprint = null;
        PopulateEditor(new StrategyProfileItem
        {
            Id = identifier,
            DisplayName = $"{template.DisplayName} Custom",
            Family = "farm",
            Tier = template.Tier,
            Version = 1,
            Editable = true,
            Loadout = template.Loadout,
            Setup = template.Setup,
        });
        EditorTitle.Text = "New custom Farm profile";
        EditorHelpText.Text =
            "Choose a unique ID, loadout policies, persistent skips, and profile settings. Validation resolves every preset and generates the complete runtime plan on Linux.";
        ValidationSummaryText.Text =
            "Draft not yet validated. Publishing will create version 1 without activating it.";
        CloneButton.IsEnabled = false;
        SetEditorEnabled(true);
        ProfileIdBox.Focus();
        ProfileIdBox.SelectAll();
    }

    private string SuggestedId(int tier)
    {
        var existing = (_catalog?.Items ?? [])
            .Select(profile => profile.Id)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var baseId = $"farm_t{tier}_custom";
        var candidate = baseId;
        for (var suffix = 2; existing.Contains(candidate); suffix++)
        {
            candidate = $"{baseId}_{suffix}";
        }
        return candidate;
    }

    private async void Validate_Click(object sender, RoutedEventArgs e)
    {
        await ValidateOrPublishAsync(publish: false);
    }

    private async void Publish_Click(object sender, RoutedEventArgs e)
    {
        var identifier = ProfileIdBox.Text.Trim();
        if (MessageBox.Show(
                this,
                $"Publish {identifier} as a selectable custom strategy profile?\n\n"
                    + "This validates and atomically stores both the compact source and generated plan. It does not activate the profile.",
                "Publish strategy profile",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question) != MessageBoxResult.Yes)
        {
            return;
        }
        await ValidateOrPublishAsync(publish: true);
    }

    private async Task ValidateOrPublishAsync(bool publish)
    {
        object profile;
        try
        {
            profile = BuildDraftPayload();
        }
        catch (Exception exc)
        {
            ShowFailure(exc.Message);
            return;
        }

        SetBusy(
            true,
            publish ? "Validating and publishing profile..." : "Validating draft...");
        try
        {
            using var cancellation = new CancellationTokenSource(
                TimeSpan.FromSeconds(120));
            var response = await _api.PostStrategyProfileAsync(
                new
                {
                    action = publish ? "publish" : "validate",
                    profile,
                    expected_source_fingerprint = publish
                        ? _expectedSourceFingerprint
                        : null,
                },
                cancellation.Token);
            ValidationSummaryText.Text = string.Join(Environment.NewLine, response.Summary);
            ValidationSummaryText.Foreground = new SolidColorBrush(
                Color.FromRgb(101, 230, 166));
            if (!publish)
            {
                StatusText.Text =
                    $"Draft is valid. The generated plan contains {response.RuleCount} rules; no files were changed.";
                StatusText.Foreground = new SolidColorBrush(
                    Color.FromRgb(101, 230, 166));
                return;
            }

            PublishedStrategyId = response.Profile.Id;
            _expectedSourceFingerprint = response.Profile.SourceFingerprint;
            StatusText.Text =
                $"Published {response.Profile.DisplayName} version {response.Profile.Version}. Select it in the main Controls pane when you want to activate it."
                + (string.IsNullOrWhiteSpace(response.Warning)
                    ? ""
                    : $" Audit warning: {response.Warning}");
            StatusText.Foreground = new SolidColorBrush(
                Color.FromRgb(101, 230, 166));
            if (response.Catalog is not null)
            {
                ApplyCatalog(response.Catalog, response.Profile.Id);
            }
            else
            {
                await LoadCatalogAsync(response.Profile.Id);
            }
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

    private object BuildDraftPayload()
    {
        if (!int.TryParse(
                TierBox.Text.Trim(),
                NumberStyles.None,
                CultureInfo.InvariantCulture,
                out var tier))
        {
            throw new InvalidOperationException("Tier must be an integer.");
        }
        var setupSettings = _setupSettings.ToDictionary(
            pair => pair.Key,
            pair => (object?)pair.Value.Clone(),
            StringComparer.Ordinal);
        setupSettings["perk_bans"] = _perkBans
            .Select(option => option.Id)
            .ToArray();
        setupSettings["perk_auto_pick_order"] = _autoPickOrder
            .Select(option => option.Id)
            .ToArray();
        return new
        {
            id = ProfileIdBox.Text.Trim(),
            display_name = DisplayNameBox.Text.Trim(),
            tier,
            setup = new
            {
                skipped_checks = SkippedChecks(),
                settings = setupSettings,
            },
            loadout = new
            {
                modules = PresetPolicy(
                    "Modules",
                    ModulesModeBox,
                    ModulesPresetBox),
                damage_slider = DamagePolicy(),
                orb_distance = PresetPolicy(
                    "Orb Distance",
                    OrbModeBox,
                    OrbPresetBox),
                target_priority = PresetPolicy(
                    "Target Priority",
                    TargetModeBox,
                    TargetPresetBox),
            },
        };
    }

    private static Dictionary<string, object?> PresetPolicy(
        string label,
        ComboBox modeBox,
        ComboBox presetBox)
    {
        var mode = SelectedMode(modeBox);
        var policy = new Dictionary<string, object?> { ["mode"] = mode };
        if (mode != "preserve")
        {
            var preset = presetBox.SelectedValue?.ToString();
            if (string.IsNullOrWhiteSpace(preset))
            {
                throw new InvalidOperationException(
                    $"{label} requires a preset in {mode} mode.");
            }
            policy["preset"] = preset;
        }
        return policy;
    }

    private Dictionary<string, object?> DamagePolicy()
    {
        var mode = SelectedMode(DamageModeBox);
        var policy = new Dictionary<string, object?> { ["mode"] = mode };
        if (mode != "preserve")
        {
            var value = DamageValueBox.Text.Trim();
            if (string.IsNullOrWhiteSpace(value))
            {
                throw new InvalidOperationException(
                    $"Damage Slider requires a value in {mode} mode.");
            }
            policy["value"] = value;
        }
        return policy;
    }

    private static string SelectedMode(ComboBox box) =>
        box.SelectedItem?.ToString()
        ?? throw new InvalidOperationException("Every loadout policy requires a mode.");

    private void SetupSkip_Changed(object sender, RoutedEventArgs e)
    {
        if (IsInitialized)
        {
            UpdateSetupInputs();
        }
    }

    private void AddBan_Click(object sender, RoutedEventArgs e)
    {
        if (_perkBans.Count >= 6)
        {
            ShowEditorMessage("Perk Bans supports at most six selected perks.");
            return;
        }
        if (AvailableBanPerkBox.SelectedItem is StrategyPresetOption option)
        {
            _perkBans.Add(option);
            RefreshAvailablePerks();
        }
    }

    private void RemoveBan_Click(object sender, RoutedEventArgs e)
    {
        if (PerkBansList.SelectedItem is StrategyPresetOption option)
        {
            _perkBans.Remove(option);
            RefreshAvailablePerks();
        }
    }

    private void AddAutoPick_Click(object sender, RoutedEventArgs e)
    {
        if (AvailableAutoPickPerkBox.SelectedItem is StrategyPresetOption option)
        {
            _autoPickOrder.Add(option);
            AutoPickOrderList.SelectedItem = option;
            AutoPickOrderList.ScrollIntoView(option);
            RefreshAvailablePerks();
        }
    }

    private void RemoveAutoPick_Click(object sender, RoutedEventArgs e)
    {
        if (AutoPickOrderList.SelectedItem is not StrategyPresetOption option)
        {
            return;
        }
        if (_autoPickOrder.Count == 1)
        {
            ShowEditorMessage("Auto Pick priority must retain at least one perk.");
            return;
        }
        var index = _autoPickOrder.IndexOf(option);
        _autoPickOrder.Remove(option);
        AutoPickOrderList.SelectedIndex = Math.Min(
            index,
            _autoPickOrder.Count - 1);
        RefreshAvailablePerks();
    }

    private void MoveAutoPickUp_Click(object sender, RoutedEventArgs e) =>
        MoveSelectedAutoPick(-1);

    private void MoveAutoPickDown_Click(object sender, RoutedEventArgs e) =>
        MoveSelectedAutoPick(1);

    private void MoveSelectedAutoPick(int offset)
    {
        var index = AutoPickOrderList.SelectedIndex;
        var destination = index + offset;
        if (index < 0 || destination < 0 || destination >= _autoPickOrder.Count)
        {
            return;
        }
        _autoPickOrder.Move(index, destination);
        AutoPickOrderList.SelectedIndex = destination;
        AutoPickOrderList.ScrollIntoView(_autoPickOrder[destination]);
        UpdatePerkButtons();
    }

    private void PerkList_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e) => UpdatePerkButtons();

    private void PolicyMode_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (IsInitialized)
        {
            UpdatePolicyInputs();
        }
    }

    private void UpdatePolicyInputs()
    {
        ModulesPresetBox.IsEnabled = IsPolicyValueEnabled(ModulesModeBox);
        DamageValueBox.IsEnabled = IsPolicyValueEnabled(DamageModeBox);
        OrbPresetBox.IsEnabled = IsPolicyValueEnabled(OrbModeBox);
        TargetPresetBox.IsEnabled = IsPolicyValueEnabled(TargetModeBox);
    }

    private void UpdateSetupInputs()
    {
        PerkBansEditor.IsEnabled =
            EditorPanel.IsEnabled && SkipPerkBansBox.IsChecked != true;
        AutoPickOrderEditor.IsEnabled =
            EditorPanel.IsEnabled && SkipAutoPickOrderBox.IsChecked != true;
        UpdatePerkButtons();
    }

    private void UpdatePerkButtons()
    {
        AddBanButton.IsEnabled = !_busy
            && PerkBansEditor.IsEnabled
            && _perkBans.Count < 6
            && AvailableBanPerkBox.SelectedItem is not null;
        RemoveBanButton.IsEnabled = !_busy
            && PerkBansEditor.IsEnabled
            && PerkBansList.SelectedItem is not null;
        AddAutoPickButton.IsEnabled = !_busy
            && AutoPickOrderEditor.IsEnabled
            && AvailableAutoPickPerkBox.SelectedItem is not null;
        var selectedIndex = AutoPickOrderList.SelectedIndex;
        RemoveAutoPickButton.IsEnabled = !_busy
            && AutoPickOrderEditor.IsEnabled
            && selectedIndex >= 0
            && _autoPickOrder.Count > 1;
        MoveAutoPickUpButton.IsEnabled = !_busy
            && AutoPickOrderEditor.IsEnabled
            && selectedIndex > 0;
        MoveAutoPickDownButton.IsEnabled = !_busy
            && AutoPickOrderEditor.IsEnabled
            && selectedIndex >= 0
            && selectedIndex < _autoPickOrder.Count - 1;
    }

    private static bool IsPolicyValueEnabled(ComboBox modeBox) =>
        !string.Equals(
            modeBox.SelectedItem?.ToString(),
            "preserve",
            StringComparison.OrdinalIgnoreCase);

    private static bool IsFarmProfile(StrategyProfileItem profile) =>
        string.Equals(profile.Family, "farm", StringComparison.OrdinalIgnoreCase);

    private void SetBusy(bool busy, string? message = null)
    {
        _busy = busy;
        ProfilesList.IsEnabled = !busy;
        CloneButton.IsEnabled = !busy
            && _selectedProfile is not null
            && IsFarmProfile(_selectedProfile);
        ValidateButton.IsEnabled = !busy && EditorPanel.IsEnabled;
        PublishButton.IsEnabled = !busy && EditorPanel.IsEnabled;
        UpdatePerkButtons();
        if (!string.IsNullOrWhiteSpace(message))
        {
            StatusText.Text = message;
            StatusText.Foreground = (Brush)FindResource("MutedBrush");
        }
    }

    private void ShowFailure(string message)
    {
        StatusText.Text = message;
        StatusText.Foreground = new SolidColorBrush(
            Color.FromRgb(255, 113, 135));
        ValidationSummaryText.Text = "Validation failed; nothing was published.";
        ValidationSummaryText.Foreground = new SolidColorBrush(
            Color.FromRgb(255, 113, 135));
    }

    private void ShowEditorMessage(string message)
    {
        StatusText.Text = message;
        StatusText.Foreground = new SolidColorBrush(
            Color.FromRgb(241, 191, 91));
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
