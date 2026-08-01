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

public sealed class AuthoringSettingRowViewModel : INotifyPropertyChanged
{
    private readonly StrategySettingDefinition _definition;
    private readonly bool _isBase;
    private readonly bool _entityEditable;
    private readonly JsonElement? _retainedValue;
    private AuthoringSourceStateDefinition? _selectedSourceState;
    private StrategyPresetOption? _selectedPreset;
    private StrategyPresetOption? _selectedPerk;
    private string _valueText = "";
    private bool _booleanValue;
    private bool _dirty;
    private StrategyResolvedSetting? _resolution;

    public AuthoringSettingRowViewModel(
        StrategySettingDefinition definition,
        bool isBase,
        bool entityEditable,
        StrategyAuthoringDirective? directive,
        StrategyResolvedSetting? resolution,
        StrategyAuthoringCapabilities capabilities,
        StrategyAuthoringEditorOptions editorOptions)
    {
        _definition = definition;
        _isBase = isBase;
        _entityEditable = entityEditable;
        _resolution = resolution;
        _retainedValue = directive?.Value?.Clone();
        PresetOptions = editorOptions.Presets.TryGetValue(
            definition.Id,
            out var presets)
            ? presets
            : [];
        AllPerks = editorOptions.Perks;
        PerkValues.CollectionChanged += (_, _) =>
        {
            Dirty = true;
            RefreshAvailablePerks();
            Notify(nameof(EffectiveValueDisplay));
        };

        ConfigureValue(directive?.Value);
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
        RefreshAvailablePerks();
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Id => _definition.Id;
    public string DisplayName => _definition.DisplayName;
    public string Section => _definition.Section;
    public string EditorType => _definition.EditorType;
    public IReadOnlyList<AuthoringSourceStateDefinition> AvailableSourceStates { get; }
    public IReadOnlyList<StrategyPresetOption> PresetOptions { get; }
    public IReadOnlyList<StrategyPresetOption> AllPerks { get; }
    public ObservableCollection<StrategyPresetOption> PerkValues { get; } = [];
    public ObservableCollection<StrategyPresetOption> AvailablePerks { get; } = [];

    public AuthoringSourceStateDefinition? SelectedSourceState
    {
        get => _selectedSourceState;
        set
        {
            if (ReferenceEquals(_selectedSourceState, value))
            {
                return;
            }
            _selectedSourceState = value;
            Dirty = true;
            Notify();
            Notify(nameof(ValueEditorEnabled));
            Notify(nameof(IsActive));
            Notify(nameof(CanResetToInherited));
            Notify(nameof(PendingEffectiveDisplay));
            Notify(nameof(EffectivePolicyDisplay));
            Notify(nameof(EffectiveValueDisplay));
            Notify(nameof(ProvenanceDisplay));
        }
    }

    public StrategyPresetOption? SelectedPreset
    {
        get => _selectedPreset;
        set
        {
            if (ReferenceEquals(_selectedPreset, value))
            {
                return;
            }
            _selectedPreset = value;
            Dirty = true;
            Notify();
            Notify(nameof(EffectiveValueDisplay));
        }
    }

    public StrategyPresetOption? SelectedPerk
    {
        get => _selectedPerk;
        set
        {
            _selectedPerk = value;
            Notify();
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
            Dirty = true;
            Notify();
            Notify(nameof(EffectiveValueDisplay));
        }
    }

    public bool BooleanValue
    {
        get => _booleanValue;
        set
        {
            if (_booleanValue == value)
            {
                return;
            }
            _booleanValue = value;
            Dirty = true;
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

    public bool UsesPresetEditor => EditorType == "preset";
    public bool UsesBooleanEditor => EditorType == "boolean";
    public bool UsesTextEditor => EditorType is "fixed_value" or "damage_percentage";
    public bool UsesPerkEditor => EditorType is "perk_multiselect" or "perk_order";
    public bool IsOrderedPerkEditor => EditorType == "perk_order";
    public bool HasSpecializedEditor =>
        UsesPresetEditor || UsesBooleanEditor || UsesTextEditor || UsesPerkEditor;
    public bool IsReadOnlyValue => !HasSpecializedEditor;

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

    public string ValueEditorExplanation => HasSpecializedEditor
        ? ""
        : "This phase has no safe value editor for this registry type. The value remains visible and round-trips unchanged; source transitions that require a new value are disabled.";

    public void ResetToInherited()
    {
        var inherit = AvailableSourceStates.FirstOrDefault(
            state => state.Id == "inherit");
        if (inherit is not null)
        {
            SelectedSourceState = inherit;
        }
    }

    public void AddSelectedPerk()
    {
        if (SelectedPerk is null)
        {
            return;
        }
        PerkValues.Add(SelectedPerk);
        SelectedPerk = null;
    }

    public void RemovePerk(StrategyPresetOption? option)
    {
        if (option is not null)
        {
            PerkValues.Remove(option);
        }
    }

    public void MovePerk(StrategyPresetOption? option, int offset)
    {
        if (option is null)
        {
            return;
        }
        var index = PerkValues.IndexOf(option);
        var destination = index + offset;
        if (index >= 0 && destination >= 0 && destination < PerkValues.Count)
        {
            PerkValues.Move(index, destination);
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
                Value = CurrentValue()?.Clone(),
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
        if (UsesPresetEditor
            && element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty("preset", out var preset))
        {
            var id = preset.GetString();
            _selectedPreset = PresetOptions.FirstOrDefault(option => option.Id == id);
        }
        else if (UsesBooleanEditor
            && element.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            _booleanValue = element.GetBoolean();
        }
        else if (UsesTextEditor)
        {
            _valueText = element.ValueKind == JsonValueKind.String
                ? element.GetString() ?? ""
                : element.GetRawText();
        }
        else if (UsesPerkEditor && element.ValueKind == JsonValueKind.Array)
        {
            foreach (var raw in element.EnumerateArray())
            {
                var id = raw.GetString();
                if (string.IsNullOrWhiteSpace(id))
                {
                    continue;
                }
                PerkValues.Add(
                    AllPerks.FirstOrDefault(option => option.Id == id)
                    ?? new StrategyPresetOption
                    {
                        Id = id,
                        DisplayName = $"{id} (preserved unknown value)",
                    });
            }
            _dirty = false;
        }
    }

    private JsonElement? CurrentValue()
    {
        if (UsesPresetEditor)
        {
            return SelectedPreset is null
                ? null
                : JsonSerializer.SerializeToElement(
                    new Dictionary<string, string>
                    {
                        ["preset"] = SelectedPreset.Id,
                    });
        }
        if (UsesBooleanEditor)
        {
            return JsonSerializer.SerializeToElement(BooleanValue);
        }
        if (UsesTextEditor)
        {
            return string.IsNullOrWhiteSpace(ValueText)
                ? null
                : JsonSerializer.SerializeToElement(ValueText.Trim());
        }
        if (UsesPerkEditor)
        {
            return JsonSerializer.SerializeToElement(
                PerkValues.Select(option => option.Id).ToArray());
        }
        return _retainedValue?.Clone();
    }

    private void RefreshAvailablePerks()
    {
        var selected = PerkValues.Select(option => option.Id).ToHashSet(
            StringComparer.Ordinal);
        AvailablePerks.Clear();
        foreach (var option in AllPerks.Where(option => !selected.Contains(option.Id)))
        {
            AvailablePerks.Add(option);
        }
        Notify(nameof(AvailablePerks));
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
                element.EnumerateArray().Select(item =>
                    item.ValueKind == JsonValueKind.String
                        ? item.GetString()
                        : item.GetRawText()));
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
        builder.AppendLine("REBASE REVIEW");
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
            "Accepting this review changes only the draft's pinned Base revision. "
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
