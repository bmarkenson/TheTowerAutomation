using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;

namespace TheTower.ControlSurface;

public sealed class StrategyBasePinChoice
{
    public string DisplayName { get; init; } = "";
    public StrategyBaseReference? Reference { get; init; }
}

public sealed record AuthoringDormantValue(JsonElement Value, bool Materialized);

public sealed class AuthoringSettingRowViewModel : INotifyPropertyChanged
{
    private readonly StrategySettingDefinition _definition;
    private readonly bool _isBase;
    private readonly bool _entityEditable;
    private readonly JsonElement? _retainedValue;
    private AuthoringSourceStateDefinition? _selectedSourceState;
    private StrategyEditorOption? _selectedPreset;
    private StrategyEditorOption? _selectedListOption;
    private StrategyEditorOption? _selectedScalarOption;
    private string _valueText = "";
    private bool _hasDormantValue;
    private bool _configuring;
    private bool _dirty;
    private StrategyResolvedSetting? _resolution;
    private readonly Dictionary<string, JsonElement> _ultimateUnknownGroups = [];

    public AuthoringSettingRowViewModel(
        StrategySettingDefinition definition,
        bool isBase,
        bool entityEditable,
        StrategyAuthoringDirective? directive,
        StrategyResolvedSetting? resolution,
        StrategyAuthoringCapabilities capabilities,
        AuthoringDormantValue? dormantValue = null)
    {
        _definition = definition;
        _isBase = isBase;
        _entityEditable = entityEditable;
        _resolution = resolution;
        var suppliedValue = directive?.Value
            ?? dormantValue?.Value
            ?? definition.InitialValue;
        _retainedValue = suppliedValue?.Clone();
        _hasDormantValue = directive?.Value.HasValue == true
            || dormantValue?.Materialized == true;
        PresetOptions = definition.Editor.Fields.FirstOrDefault()?.Options ?? [];
        AllListOptions = definition.Editor.Options;
        ListValues.CollectionChanged += (_, _) =>
        {
            if (!_configuring)
            {
                MarkValueChanged();
            }
            RefreshAvailableListOptions();
            Notify(nameof(EffectiveValueDisplay));
            Notify(nameof(CanAddListItem));
            Notify(nameof(CanRemoveListItem));
        };

        _configuring = true;
        ConfigureValue(_retainedValue);
        _configuring = false;
        var states = isBase
            ? capabilities.BaseSourceStates
            : capabilities.StrategySourceStates;
        AvailableSourceStates = states
            .Where(state => StateCanBeRepresented(state, directive))
            .ToList();
        var selectedId = SourceStateId(directive);
        _selectedSourceState = AvailableSourceStates.FirstOrDefault(
            state => state.Id == selectedId)
            ?? AvailableSourceStates.FirstOrDefault();
        RefreshAvailableListOptions();
        _dirty = false;
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Id => _definition.Id;
    public string DisplayName => _definition.DisplayName;
    public string Section => _definition.Section;
    public string EditorType => _definition.EditorType;
    public IReadOnlyList<AuthoringSourceStateDefinition> AvailableSourceStates { get; }
    public IReadOnlyList<StrategyEditorOption> PresetOptions { get; }
    public IReadOnlyList<StrategyEditorOption> AllListOptions { get; }
    public ObservableCollection<StrategyEditorOption> ListValues { get; } = [];
    public ObservableCollection<StrategyEditorOption> AvailableListOptions { get; } = [];
    public ObservableCollection<AuthoringChoiceFieldViewModel> ChoiceFields { get; } = [];
    public ObservableCollection<AuthoringToggleGroupViewModel> UltimateGroups { get; } = [];

    public AuthoringSourceStateDefinition? SelectedSourceState
    {
        get => _selectedSourceState;
        set
        {
            if (ReferenceEquals(_selectedSourceState, value)
                || value is null
                || !AvailableSourceStates.Contains(value))
            {
                return;
            }
            _selectedSourceState = value;
            if (value?.Policy is "enforce" or "observe")
            {
                _hasDormantValue = true;
            }
            Dirty = true;
            Notify();
            Notify(nameof(ValueEditorEnabled));
            Notify(nameof(BooleanControlEnabled));
            Notify(nameof(PresetControlEnabled));
            Notify(nameof(CanAddListItem));
            Notify(nameof(CanRemoveListItem));
            Notify(nameof(CanReorderListItems));
            Notify(nameof(IsActive));
            Notify(nameof(CanResetToInherited));
            Notify(nameof(PendingEffectiveDisplay));
            Notify(nameof(EffectivePolicyDisplay));
            Notify(nameof(EffectiveValueDisplay));
            Notify(nameof(ProvenanceDisplay));
        }
    }

    public StrategyEditorOption? SelectedPreset
    {
        get => _selectedPreset;
        set
        {
            if (ReferenceEquals(_selectedPreset, value)
                || value is null
                || !PresetOptions.Contains(value))
            {
                return;
            }
            _selectedPreset = value;
            MarkValueChanged();
            Notify();
            Notify(nameof(EffectiveValueDisplay));
        }
    }

    public StrategyEditorOption? SelectedListOption
    {
        get => _selectedListOption;
        set
        {
            if (value is not null && !AvailableListOptions.Contains(value))
            {
                return;
            }
            _selectedListOption = value;
            Notify();
            Notify(nameof(CanAddListItem));
        }
    }

    public string ValueText
    {
        get => _valueText;
        set
        {
            if (_valueText == value)
            {
                return;
            }
            _valueText = value;
            MarkValueChanged();
            Notify();
            Notify(nameof(EffectiveValueDisplay));
        }
    }

    public bool BooleanValue
    {
        get => _selectedScalarOption?.Value.ValueKind == JsonValueKind.True;
        set
        {
            var desired = JsonSerializer.SerializeToElement(value);
            var option = EditorJson.FindOption(_definition.Editor.Options, desired);
            if (option is null || ReferenceEquals(_selectedScalarOption, option))
            {
                return;
            }
            _selectedScalarOption = option;
            MarkValueChanged();
            Notify();
            Notify(nameof(EffectiveValueDisplay));
        }
    }

    public bool Dirty
    {
        get => _dirty;
        private set
        {
            if (_dirty == value)
            {
                return;
            }
            _dirty = value;
            Notify();
            Notify(nameof(PendingEffectiveDisplay));
        }
    }

    public bool UsesFixedValueEditor => EditorType == "fixed_value";
    public bool UsesPresetEditor => EditorType == "preset";
    public bool UsesBooleanEditor => EditorType == "boolean";
    public bool UsesTextEditor => EditorType == "damage_percentage";
    public bool UsesKeyedChoiceEditor => EditorType == "card_recharge_modes";
    public bool UsesListEditor => EditorType is
        "ordered_list" or "perk_multiselect" or "perk_order";
    public bool UsesUltimateWeaponEditor => EditorType == "ultimate_weapon_toggles";
    public bool HasSpecializedEditor =>
        UsesFixedValueEditor
        || UsesPresetEditor
        || UsesBooleanEditor
        || UsesTextEditor
        || UsesKeyedChoiceEditor
        || UsesListEditor
        || UsesUltimateWeaponEditor;
    public bool IsReadOnlyValue => !HasSpecializedEditor;
    public bool BooleanControlEnabled =>
        ValueEditorEnabled
        && !_definition.Editor.Fixed
        && _definition.Editor.Options.Count > 1;
    public bool PresetControlEnabled =>
        ValueEditorEnabled && !_definition.Editor.Fixed;
    public bool IsFixedPresentation => _definition.Editor.Fixed;
    public bool CanAddListItem =>
        ValueEditorEnabled
        && (_definition.Editor.ListConstraints?.AllowAdd ?? false)
        && ListValues.Count < (_definition.Editor.ListConstraints?.MaximumItems ?? 0)
        && SelectedListOption is not null;
    public bool CanRemoveListItem =>
        ValueEditorEnabled
        && (_definition.Editor.ListConstraints?.AllowRemove ?? false)
        && ListValues.Count > (_definition.Editor.ListConstraints?.MinimumItems ?? 0);
    public bool CanReorderListItems =>
        ValueEditorEnabled
        && (_definition.Editor.ListConstraints?.AllowReorder ?? false);
    public bool ListMembershipEditable =>
        (_definition.Editor.ListConstraints?.AllowAdd ?? false)
        || (_definition.Editor.ListConstraints?.AllowRemove ?? false);
    public bool ListReorderAvailable =>
        _definition.Editor.ListConstraints?.AllowReorder ?? false;
    public bool ListOrderSignificant =>
        _definition.Editor.ListConstraints?.OrderSignificant ?? false;
    public string FixedValueDisplay => _selectedScalarOption?.DisplayName
        ?? FormatJson(_definition.InitialValue);
    public string ListConstraintDisplay
    {
        get
        {
            var constraints = _definition.Editor.ListConstraints;
            if (constraints is null)
            {
                return "";
            }
            if (constraints.ExactItems.Count > 0)
            {
                return constraints.AllowReorder
                    ? $"Exact {constraints.ExactItems.Count}-item membership; order may be changed."
                    : $"Fixed exact {constraints.ExactItems.Count}-item value.";
            }
            var order = constraints.OrderSignificant
                ? "Order is significant."
                : "Order is not significant.";
            return $"{constraints.MinimumItems}–{constraints.MaximumItems} unique item(s). {order}";
        }
    }
    public string DependencyDisplay => _definition.DependencyDisplayNames.Count == 0
        ? ""
        : "Requires effective: "
            + string.Join(", ", _definition.DependencyDisplayNames)
            + ".";
    public string UnknownRetainedDisplay => _ultimateUnknownGroups.Count == 0
        ? ""
        : "Retained unrecognized weapons: "
            + string.Join(", ", _ultimateUnknownGroups.Keys.Order())
            + ".";

    public bool SourceStateEnabled => _entityEditable && AvailableSourceStates.Count > 1;

    public bool ValueEditorEnabled =>
        _entityEditable
        && HasSpecializedEditor
        && SelectedSourceState?.Policy is "enforce" or "observe";

    public bool CanResetToInherited =>
        !_isBase
        && _entityEditable
        && SelectedSourceState?.Id != "inherit";

    public bool IsActive
    {
        get
        {
            if (_isBase)
            {
                return SelectedSourceState?.Id != "not_included";
            }
            return SelectedSourceState?.Id != "inherit"
                || _resolution?.State is "effective" or "ignored";
        }
    }

    public string CapabilityDisplay =>
        $"Observation: {(_definition.ObservationSupported ? "available" : "unavailable")}"
        + $" • Repair: {(_definition.RepairSupported ? "available" : "unavailable")}";

    public string EffectivePolicyDisplay => _resolution?.State switch
        {
            "ignored" => "Ignored",
            "unmanaged" => "Unmanaged",
            _ => Title(_resolution?.Policy),
        };

    public string EffectiveValueDisplay => _resolution?.State == "effective"
        ? FormatJson(_resolution.Value)
        : "—";

    public string ProvenanceDisplay => _resolution?.Provenance.Kind switch
        {
            "base" =>
                $"{_resolution.Provenance.BaseId} revision {_resolution.Provenance.Revision}",
            "local" => "Local Strategy override",
            "local_ignore" => "Explicit local Ignore",
            _ => _isBase ? "Not included in this Base" : "Unmanaged",
        };

    public string PendingEffectiveDisplay => Dirty
        ? "Draft changed — validate to refresh effective value and provenance."
        : "";

    public string ValueEditorExplanation => string.Join(
        " ",
        new[] { _definition.Editor.HelpText, DependencyDisplay }
            .Where(value => !string.IsNullOrWhiteSpace(value)));

    public void ResetToInherited()
    {
        var inherit = AvailableSourceStates.FirstOrDefault(
            state => state.Id == "inherit");
        if (inherit is not null)
        {
            SelectedSourceState = inherit;
        }
    }

    public void AddSelectedListItem()
    {
        if (!CanAddListItem || SelectedListOption is null)
        {
            return;
        }
        ListValues.Add(SelectedListOption);
        SelectedListOption = null;
    }

    public void RemoveListItem(StrategyEditorOption? option)
    {
        if (option is not null && CanRemoveListItem)
        {
            ListValues.Remove(option);
        }
    }

    public void MoveListItem(StrategyEditorOption? option, int offset)
    {
        if (option is null || !CanReorderListItems)
        {
            return;
        }
        var index = ListValues.IndexOf(option);
        var destination = index + offset;
        if (index >= 0 && destination >= 0 && destination < ListValues.Count)
        {
            ListValues.Move(index, destination);
            Dirty = true;
        }
    }

    public StrategyAuthoringDirective? BuildDirective()
    {
        var policy = SelectedSourceState?.Policy;
        if (policy is null)
        {
            return null;
        }
        if (policy == "ignore")
        {
            return new StrategyAuthoringDirective
            {
                Policy = policy,
                Value = _hasDormantValue ? CurrentValue()?.Clone() : null,
            };
        }

        var value = CurrentValue();
        if (value is null)
        {
            throw new InvalidOperationException(
                $"{DisplayName} requires a value for {SelectedSourceState?.DisplayName}.");
        }
        return new StrategyAuthoringDirective
        {
            Policy = policy,
            Value = value.Value.Clone(),
        };
    }

    public AuthoringDormantValue CaptureDormantValue()
    {
        var value = CurrentValue() ?? _retainedValue ?? _definition.InitialValue;
        if (!value.HasValue)
        {
            throw new InvalidOperationException(
                $"{DisplayName} has no server-supplied initial value.");
        }
        return new AuthoringDormantValue(
            value.Value.Clone(),
            _hasDormantValue);
    }

    public void ApplyResolution(StrategyResolvedSetting? resolution)
    {
        _resolution = resolution;
        Dirty = false;
        Notify(nameof(EffectivePolicyDisplay));
        Notify(nameof(EffectiveValueDisplay));
        Notify(nameof(ProvenanceDisplay));
        Notify(nameof(PendingEffectiveDisplay));
        Notify(nameof(IsActive));
    }

    private bool StateCanBeRepresented(
        AuthoringSourceStateDefinition state,
        StrategyAuthoringDirective? directive)
    {
        if (state.Policy is null)
        {
            return true;
        }
        if (!_definition.AllowedPolicies.Contains(state.Policy, StringComparer.Ordinal))
        {
            return false;
        }
        if (state.Policy == "ignore" || HasSpecializedEditor)
        {
            return true;
        }
        return directive?.Policy == state.Policy && directive.Value.HasValue;
    }

    private string SourceStateId(StrategyAuthoringDirective? directive)
    {
        if (directive is null)
        {
            return _isBase ? "not_included" : "inherit";
        }
        if (!_isBase && directive.Policy == "ignore")
        {
            return "ignore";
        }
        return _isBase
            ? $"included_{directive.Policy}"
            : $"override_{directive.Policy}";
    }

    private void ConfigureValue(JsonElement? value)
    {
        if (!value.HasValue)
        {
            return;
        }
        var element = value.Value;
        if (UsesFixedValueEditor || UsesBooleanEditor)
        {
            _selectedScalarOption = EditorJson.FindOption(
                    _definition.Editor.Options,
                    element)
                ?? _definition.Editor.Options.FirstOrDefault();
        }
        else if (UsesPresetEditor && element.ValueKind == JsonValueKind.Object)
        {
            var field = _definition.Editor.Fields.FirstOrDefault();
            if (field is not null
                && element.TryGetProperty(field.Key, out var preset))
            {
                _selectedPreset = EditorJson.FindOption(field.Options, preset)
                    ?? field.Options.FirstOrDefault();
            }
        }
        else if (UsesTextEditor)
        {
            _valueText = element.ValueKind == JsonValueKind.String
                ? element.GetString() ?? ""
                : element.GetRawText();
        }
        else if (UsesListEditor && element.ValueKind == JsonValueKind.Array)
        {
            foreach (var raw in element.EnumerateArray())
            {
                ListValues.Add(
                    EditorJson.FindOption(AllListOptions, raw)
                    ?? new StrategyEditorOption
                    {
                        Value = raw.Clone(),
                        DisplayName = $"{FormatJson(raw)} (preserved value)",
                    });
            }
        }
        else if (UsesKeyedChoiceEditor && element.ValueKind == JsonValueKind.Object)
        {
            foreach (var field in _definition.Editor.Fields)
            {
                JsonElement? current = element.TryGetProperty(field.Key, out var raw)
                    ? raw
                    : field.InitialValue;
                ChoiceFields.Add(
                    new AuthoringChoiceFieldViewModel(
                        field,
                        current,
                        MarkValueChanged));
            }
        }
        else if (UsesUltimateWeaponEditor && element.ValueKind == JsonValueKind.Object)
        {
            ConfigureUltimateWeapons(element);
        }
    }

    private JsonElement? CurrentValue()
    {
        if (UsesFixedValueEditor || UsesBooleanEditor)
        {
            return _selectedScalarOption?.Value.Clone();
        }
        if (UsesPresetEditor)
        {
            var field = _definition.Editor.Fields.FirstOrDefault();
            return SelectedPreset is null || field is null
                ? null
                : JsonSerializer.SerializeToElement(
                    new Dictionary<string, JsonElement>
                    {
                        [field.Key] = SelectedPreset.Value.Clone(),
                    });
        }
        if (UsesTextEditor)
        {
            return string.IsNullOrWhiteSpace(ValueText)
                ? null
                : JsonSerializer.SerializeToElement(ValueText.Trim());
        }
        if (UsesListEditor)
        {
            return JsonSerializer.SerializeToElement(
                ListValues.Select(option => option.Value.Clone()).ToArray());
        }
        if (UsesKeyedChoiceEditor)
        {
            var fields = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
            foreach (var field in ChoiceFields)
            {
                if (field.CurrentValue is { } current)
                {
                    fields[field.Key] = current.Clone();
                }
            }
            return JsonSerializer.SerializeToElement(fields);
        }
        if (UsesUltimateWeaponEditor)
        {
            return BuildUltimateWeaponValue();
        }
        return _retainedValue?.Clone();
    }

    private void ConfigureUltimateWeapons(JsonElement element)
    {
        var current = EditorJson.ObjectValues(element);
        var knownGroups = _definition.Editor.Groups
            .Select(group => group.Key)
            .ToHashSet(StringComparer.Ordinal);
        foreach (var (key, retained) in current)
        {
            if (!knownGroups.Contains(key))
            {
                _ultimateUnknownGroups[key] = retained.Clone();
            }
        }
        foreach (var group in _definition.Editor.Groups)
        {
            current.TryGetValue(group.Key, out var rawGroup);
            UltimateGroups.Add(
                new AuthoringToggleGroupViewModel(
                    group,
                    current.ContainsKey(group.Key) ? rawGroup : null,
                    UltimateValueChanged,
                    CanExcludeUltimateGroup));
        }
        RefreshUltimateConstraints();
    }

    private JsonElement BuildUltimateWeaponValue()
    {
        var result = _ultimateUnknownGroups.ToDictionary(
            item => item.Key,
            item => item.Value.Clone(),
            StringComparer.Ordinal);
        foreach (var group in UltimateGroups)
        {
            var fields = group.BuildFields();
            if (fields.Count > 0)
            {
                result[group.Key] = JsonSerializer.SerializeToElement(fields);
            }
        }
        return JsonSerializer.SerializeToElement(result);
    }

    private bool CanExcludeUltimateGroup(AuthoringToggleGroupViewModel group)
    {
        var remaining = UltimateGroups.Count(candidate =>
            !ReferenceEquals(candidate, group) && candidate.IsEffectivelyPresent);
        if (group.HasUnknownFields)
        {
            remaining++;
        }
        return remaining >= _definition.Editor.MinimumSelectedGroups;
    }

    private void UltimateValueChanged()
    {
        MarkValueChanged();
        RefreshUltimateConstraints();
    }

    private void RefreshUltimateConstraints()
    {
        foreach (var group in UltimateGroups)
        {
            group.RefreshConstraints();
        }
        Notify(nameof(UnknownRetainedDisplay));
    }

    private void RefreshAvailableListOptions()
    {
        var selected = ListValues.Select(option => option.ValueKey).ToHashSet(
            StringComparer.Ordinal);
        AvailableListOptions.Clear();
        foreach (var option in AllListOptions.Where(option =>
                     !selected.Contains(option.ValueKey)))
        {
            AvailableListOptions.Add(option);
        }
        Notify(nameof(AvailableListOptions));
        Notify(nameof(CanAddListItem));
        Notify(nameof(CanRemoveListItem));
    }

    private void MarkValueChanged()
    {
        if (_configuring)
        {
            return;
        }
        _hasDormantValue = true;
        Dirty = true;
    }

    private static string FormatJson(JsonElement? value)
    {
        if (!value.HasValue)
        {
            return "—";
        }
        var element = value.Value;
        if (element.ValueKind == JsonValueKind.String)
        {
            return element.GetString() ?? "—";
        }
        if (element.ValueKind == JsonValueKind.Array)
        {
            return string.Join(
                ", ",
                element.EnumerateArray().Select(item => FormatJson(item)));
        }
        if (element.ValueKind == JsonValueKind.Object)
        {
            return string.Join(
                "; ",
                element.EnumerateObject().Select(property =>
                    $"{property.Name}: {FormatJson(property.Value)}"));
        }
        if (element.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            return element.GetBoolean() ? "Enabled" : "Disabled";
        }
        return element.GetRawText();
    }

    private static string Title(string? value) => string.IsNullOrWhiteSpace(value)
        ? "Unmanaged"
        : char.ToUpperInvariant(value[0]) + value[1..];

    private void Notify([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}

public static class StrategyAuthoringReviewFormatter
{
    public static string FormatPublishReview(
        StrategyAuthoringMutationResponse response,
        bool isBase)
    {
        var source = response.Review.SourceChanges;
        var effective = response.Review.EffectiveChanges;
        var builder = new StringBuilder();
        builder.AppendLine(isBase ? "BASE PUBLICATION REVIEW" : "STRATEGY PUBLICATION REVIEW");
        builder.AppendLine();
        builder.AppendLine(
            $"Source changes: {source?.ChangeCount ?? 0} "
            + $"({source?.Added.Count ?? 0} added, {source?.Removed.Count ?? 0} removed, "
            + $"{source?.Changed.Count ?? 0} changed)");
        if (source is not null)
        {
            AppendNames(builder, "Added", source.Added.Select(item => item.DisplayName));
            AppendNames(builder, "Removed", source.Removed.Select(item => item.DisplayName));
            AppendNames(builder, "Changed", source.Changed.Select(item => item.DisplayName));
            AppendNames(
                builder,
                "Metadata",
                source.MetadataChanges.Select(item => item.Label));
        }
        if (!isBase)
        {
            builder.AppendLine(
                $"Effective resolved changes: {effective?.ChangeCount ?? 0}; "
                + $"provenance-only changes: {effective?.ProvenanceChanged.Count ?? 0}");
            if (effective is not null)
            {
                AppendNames(
                    builder,
                    "Effective",
                    effective.Changed.Select(item => item.DisplayName));
                AppendNames(
                    builder,
                    "Provenance only",
                    effective.ProvenanceChanged.Select(item => item.DisplayName));
            }
            builder.AppendLine($"Generated rule count: {response.RuleCount}");
        }
        builder.AppendLine(
            response.Review.Validation.Valid
                ? "Validation: passed"
                : "Validation: failed — "
                    + string.Join(
                        "; ",
                        response.Review.Validation.Errors.Select(error => error.Message)));
        if (response.Fingerprints.Count > 0)
        {
            builder.AppendLine("Fingerprints:");
            foreach (var fingerprint in response.Fingerprints.OrderBy(item => item.Key))
            {
                builder.AppendLine($"  {fingerprint.Key}: {fingerprint.Value}");
            }
        }
        builder.AppendLine();
        builder.AppendLine(isBase
            ? "Publishing creates a new immutable Base revision. Bases cannot be activated."
            : "Publishing will not activate this Strategy. Activation remains a separate action in the main Controls pane.");
        return builder.ToString().TrimEnd();
    }

    public static string FormatRebaseReview(StrategyAuthoringMutationResponse response)
    {
        var rebase = response.Rebase
            ?? throw new InvalidOperationException("The server did not return a rebase review.");
        var builder = new StringBuilder();
        builder.AppendLine("BASE PIN REVIEW");
        builder.AppendLine();
        builder.AppendLine(
            $"Base settings: {rebase.BaseChanges.Added.Count} added, "
            + $"{rebase.BaseChanges.Removed.Count} removed, "
            + $"{rebase.BaseChanges.Changed.Count} changed");
        AppendNames(
            builder,
            "Base added",
            rebase.BaseChanges.Added.Select(item => item.DisplayName));
        AppendNames(
            builder,
            "Base removed",
            rebase.BaseChanges.Removed.Select(item => item.DisplayName));
        AppendNames(
            builder,
            "Base changed",
            rebase.BaseChanges.Changed.Select(item => item.DisplayName));
        builder.AppendLine(
            $"Inherited effective values changing: {rebase.InheritedEffectiveChanges.Count}");
        AppendNames(
            builder,
            "Inherited changes",
            rebase.InheritedEffectiveChanges.Select(item => item.DisplayName));
        builder.AppendLine(
            $"Local overrides remaining unchanged: {rebase.LocalOverridesUnchanged.Count}");
        AppendNames(
            builder,
            "Local overrides",
            rebase.LocalOverridesUnchanged.Select(item => item.DisplayName));
        builder.AppendLine(
            $"Explicit ignores remaining ignored: {rebase.ExplicitIgnoresUnchanged.Count}");
        AppendNames(
            builder,
            "Explicit ignores",
            rebase.ExplicitIgnoresUnchanged.Select(item => item.DisplayName));
        builder.AppendLine(
            rebase.ValidationErrors.Count == 0
                ? "Resulting validation: passed"
                : "Resulting validation errors:\n  "
                    + string.Join(
                        "\n  ",
                        rebase.ValidationErrors.Select(error => error.Message)));
        builder.AppendLine();
        builder.AppendLine(
            "Accepting this review changes only the draft's pinned Base reference. "
            + "The Strategy must still be validated and published, and publishing will not activate it.");
        return builder.ToString().TrimEnd();
    }

    private static void AppendNames(
        StringBuilder builder,
        string label,
        IEnumerable<string> names)
    {
        var visible = names
            .Where(name => !string.IsNullOrWhiteSpace(name))
            .ToArray();
        if (visible.Length > 0)
        {
            builder.AppendLine($"  {label}: {string.Join(", ", visible)}");
        }
    }
}
