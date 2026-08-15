using System.ComponentModel;
using System.Collections.ObjectModel;
using System.Runtime.CompilerServices;
using System.Text.Json;

namespace TheTower.ControlSurface;

public sealed class AuthoringChoiceFieldViewModel : INotifyPropertyChanged
{
    private readonly Action _changed;
    private StrategyEditorOption? _selectedOption;

    public AuthoringChoiceFieldViewModel(
        StrategyEditorField definition,
        JsonElement? currentValue,
        Action changed)
    {
        Definition = definition;
        _changed = changed;
        _selectedOption = EditorJson.FindOption(
                definition.Options,
                currentValue)
            ?? EditorJson.FindOption(
                definition.Options,
                definition.InitialValue)
            ?? definition.Options.FirstOrDefault();
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public StrategyEditorField Definition { get; }
    public string Key => Definition.Key;
    public string DisplayName => Definition.DisplayName;
    public bool Fixed => Definition.Fixed;
    public bool SelectionEnabled => !Fixed;
    public IReadOnlyList<StrategyEditorOption> Options => Definition.Options;

    public StrategyEditorOption? SelectedOption
    {
        get => _selectedOption;
        set
        {
            if (ReferenceEquals(_selectedOption, value)
                || value is null
                || !Options.Contains(value))
            {
                return;
            }
            _selectedOption = value;
            Notify();
            _changed();
        }
    }

    public JsonElement? CurrentValue => SelectedOption?.Value.Clone();

    private void Notify([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}

public sealed class AuthoringToggleFieldViewModel : INotifyPropertyChanged
{
    private readonly Action _changed;
    private readonly Func<AuthoringToggleFieldViewModel, bool> _canExclude;
    private StrategyEditorOption? _selectedOption;
    private bool _included;

    public AuthoringToggleFieldViewModel(
        StrategyEditorField definition,
        JsonElement? currentValue,
        bool included,
        Action changed,
        Func<AuthoringToggleFieldViewModel, bool> canExclude)
    {
        Definition = definition;
        _changed = changed;
        _canExclude = canExclude;
        _included = included;
        _selectedOption = EditorJson.FindOption(
                definition.Options,
                currentValue)
            ?? EditorJson.FindOption(
                definition.Options,
                definition.InitialValue)
            ?? definition.Options.FirstOrDefault();
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public StrategyEditorField Definition { get; }
    public string Key => Definition.Key;
    public string DisplayName => Definition.DisplayName;
    public bool Fixed => Definition.Fixed;
    public IReadOnlyList<StrategyEditorOption> Options => Definition.Options;

    public bool IsIncluded
    {
        get => _included;
        set
        {
            if (_included == value || (!value && !_canExclude(this)))
            {
                return;
            }
            _included = value;
            Notify();
            Notify(nameof(InclusionEnabled));
            Notify(nameof(SelectionEnabled));
            _changed();
        }
    }

    public bool InclusionEnabled => !IsIncluded || _canExclude(this);
    public bool SelectionEnabled => IsIncluded && !Fixed;

    public StrategyEditorOption? SelectedOption
    {
        get => _selectedOption;
        set
        {
            if (ReferenceEquals(_selectedOption, value)
                || value is null
                || !Options.Contains(value))
            {
                return;
            }
            _selectedOption = value;
            Notify();
            _changed();
        }
    }

    public JsonElement? CurrentValue => SelectedOption?.Value.Clone();

    internal void SetIncludedDirect(bool included)
    {
        if (_included == included)
        {
            return;
        }
        _included = included;
        Notify(nameof(IsIncluded));
        Notify(nameof(InclusionEnabled));
        Notify(nameof(SelectionEnabled));
    }

    internal void RefreshConstraints()
    {
        Notify(nameof(InclusionEnabled));
        Notify(nameof(SelectionEnabled));
    }

    private void Notify([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}

public sealed class AuthoringToggleGroupViewModel : INotifyPropertyChanged
{
    private readonly Action _changed;
    private readonly Func<AuthoringToggleGroupViewModel, bool> _canExclude;
    private readonly Dictionary<string, JsonElement> _unknownFields = [];
    private bool _suppressFieldChanges;

    public AuthoringToggleGroupViewModel(
        StrategyEditorGroup definition,
        JsonElement? currentValue,
        Action changed,
        Func<AuthoringToggleGroupViewModel, bool> canExclude)
    {
        Definition = definition;
        _changed = changed;
        _canExclude = canExclude;
        var current = EditorJson.ObjectValues(currentValue);
        var knownKeys = definition.Fields
            .Select(field => field.Key)
            .ToHashSet(StringComparer.Ordinal);
        foreach (var (key, value) in current)
        {
            if (!knownKeys.Contains(key))
            {
                _unknownFields[key] = value.Clone();
            }
        }
        foreach (var field in definition.Fields)
        {
            current.TryGetValue(field.Key, out var value);
            var included = current.ContainsKey(field.Key);
            Fields.Add(
                new AuthoringToggleFieldViewModel(
                    field,
                    included ? value : null,
                    included,
                    FieldChanged,
                    CanExcludeField));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public StrategyEditorGroup Definition { get; }
    public string Key => Definition.Key;
    public string DisplayName => Definition.DisplayName;
    public List<AuthoringToggleFieldViewModel> Fields { get; } = [];
    public bool HasUnknownFields => _unknownFields.Count > 0;
    public bool IsEffectivelyPresent => IsIncluded || HasUnknownFields;

    public bool IsIncluded
    {
        get => Fields.Any(field => field.IsIncluded);
        set
        {
            if (IsIncluded == value || (!value && !_canExclude(this)))
            {
                return;
            }
            _suppressFieldChanges = true;
            try
            {
                foreach (var field in Fields)
                {
                    field.SetIncludedDirect(value);
                }
            }
            finally
            {
                _suppressFieldChanges = false;
            }
            RefreshConstraints();
            _changed();
        }
    }

    public bool InclusionEnabled =>
        Definition.AllowSelection && (!IsIncluded || _canExclude(this));

    public string UnknownRetainedDisplay => HasUnknownFields
        ? "Retained fields: " + string.Join(", ", _unknownFields.Keys.Order())
        : "";

    public Dictionary<string, JsonElement> BuildFields()
    {
        var result = _unknownFields.ToDictionary(
            item => item.Key,
            item => item.Value.Clone(),
            StringComparer.Ordinal);
        foreach (var field in Fields.Where(field => field.IsIncluded))
        {
            if (field.CurrentValue is { } value)
            {
                result[field.Key] = value.Clone();
            }
        }
        return result;
    }

    internal void RefreshConstraints()
    {
        Notify(nameof(IsIncluded));
        Notify(nameof(IsEffectivelyPresent));
        Notify(nameof(InclusionEnabled));
        foreach (var field in Fields)
        {
            field.RefreshConstraints();
        }
    }

    private bool CanExcludeField(AuthoringToggleFieldViewModel field)
    {
        var remaining = Fields.Count(candidate =>
            candidate.IsIncluded && !ReferenceEquals(candidate, field));
        remaining += _unknownFields.Count;
        return remaining >= Definition.MinimumSelectedFields;
    }

    private void FieldChanged()
    {
        if (_suppressFieldChanges)
        {
            return;
        }
        RefreshConstraints();
        _changed();
    }

    private void Notify([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}

public sealed record AuthoringDefinitionForm(string Key, string DisplayName);

public sealed class AuthoringLocalFieldViewModel : INotifyPropertyChanged
{
    private readonly Action _changed;
    private StrategyEditorOption? _selectedOption;
    private string _valueText = "";

    public AuthoringLocalFieldViewModel(
        StrategyEditorField definition,
        JsonElement? currentValue,
        bool serverNormalizedText,
        Action changed)
    {
        Definition = definition;
        ServerNormalizedText = serverNormalizedText;
        _changed = changed;
        if (UsesChoiceEditor)
        {
            _selectedOption = EditorJson.FindOption(
                    definition.Options,
                    currentValue)
                ?? EditorJson.FindOption(
                    definition.Options,
                    definition.InitialValue);
            foreach (var option in definition.Options)
            {
                AvailableOptions.Add(option);
            }
        }
        else
        {
            var value = currentValue ?? definition.InitialValue;
            _valueText = value.ValueKind == JsonValueKind.String
                ? value.GetString() ?? ""
                : value.GetRawText();
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public StrategyEditorField Definition { get; }
    public string Key => Definition.Key;
    public string DisplayName => Definition.DisplayName;
    public bool Fixed => Definition.Fixed;
    public bool ServerNormalizedText { get; }
    public bool UsesChoiceEditor => Definition.Options.Count > 0;
    public bool UsesTextEditor => !UsesChoiceEditor && ServerNormalizedText;
    public bool SelectionEnabled => !Fixed;
    public ObservableCollection<StrategyEditorOption> AvailableOptions { get; } = [];

    public StrategyEditorOption? SelectedOption
    {
        get => _selectedOption;
        set
        {
            if (ReferenceEquals(_selectedOption, value)
                || value is null
                || !AvailableOptions.Contains(value))
            {
                return;
            }
            _selectedOption = value;
            Notify();
            _changed();
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
            Notify();
            _changed();
        }
    }

    public JsonElement? CurrentValue => UsesChoiceEditor
        ? SelectedOption?.Value.Clone()
        : UsesTextEditor
            ? JsonSerializer.SerializeToElement(ValueText)
            : null;

    internal void RefreshAvailableOptions(IReadOnlySet<string> selectedByOtherFields)
    {
        if (!UsesChoiceEditor)
        {
            return;
        }
        var currentKey = SelectedOption?.ValueKey;
        var desiredOptions = Definition.Options.Where(option =>
                option.ValueKey == currentKey
                || !selectedByOtherFields.Contains(option.ValueKey))
            .ToArray();
        for (var desiredIndex = 0; desiredIndex < desiredOptions.Length; desiredIndex++)
        {
            var option = desiredOptions[desiredIndex];
            var currentIndex = AvailableOptions.IndexOf(option);
            if (currentIndex < 0)
            {
                AvailableOptions.Insert(desiredIndex, option);
            }
            else if (currentIndex != desiredIndex)
            {
                AvailableOptions.Move(currentIndex, desiredIndex);
            }
        }
        // The desired prefix includes the current selection. Trim only after it
        // is in place so every collection event leaves SelectedOption available.
        for (var index = AvailableOptions.Count - 1;
             index >= desiredOptions.Length;
             index--)
        {
            AvailableOptions.RemoveAt(index);
        }
    }

    private void Notify([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}

public sealed class AuthoringLocalDefinitionViewModel : INotifyPropertyChanged
{
    private readonly StrategyEditorMetadata _metadata;
    private readonly Action _changed;
    private StrategyEditorOption? _selectedListOption;
    private bool _configuring;

    public AuthoringLocalDefinitionViewModel(
        StrategyEditorMetadata metadata,
        JsonElement? currentValue,
        Action changed)
    {
        _metadata = metadata;
        _changed = changed;
        _configuring = true;
        var value = currentValue ?? metadata.InitialValue;
        if (UsesObjectEditor)
        {
            var current = EditorJson.ObjectValues(value);
            foreach (var field in metadata.Fields)
            {
                JsonElement? fieldValue = current.TryGetValue(field.Key, out var raw)
                    ? raw
                    : field.InitialValue;
                Fields.Add(
                    new AuthoringLocalFieldViewModel(
                        field,
                        fieldValue,
                        metadata.ServerNormalizedText,
                        FieldChanged));
            }
            RefreshUniqueFieldOptions();
        }
        else if (UsesListEditor && value is { ValueKind: JsonValueKind.Array })
        {
            foreach (var raw in value.Value.EnumerateArray())
            {
                ListValues.Add(
                    EditorJson.FindOption(metadata.Options, raw)
                    ?? new StrategyEditorOption
                    {
                        Value = raw.Clone(),
                        DisplayName = $"{raw.GetRawText()} (preserved value)",
                    });
            }
            ListValues.CollectionChanged += (_, _) => ListChanged();
            RefreshAvailableListOptions();
        }
        _configuring = false;
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public bool UsesObjectEditor => _metadata.ValueKind == "object";
    public bool UsesListEditor => _metadata.ValueKind == "array";
    public string HelpText => _metadata.HelpText;
    public ObservableCollection<AuthoringLocalFieldViewModel> Fields { get; } = [];
    public ObservableCollection<StrategyEditorOption> ListValues { get; } = [];
    public ObservableCollection<StrategyEditorOption> AvailableListOptions { get; } = [];

    public StrategyEditorOption? SelectedListOption
    {
        get => _selectedListOption;
        set
        {
            if (ReferenceEquals(_selectedListOption, value)
                || (value is not null && !AvailableListOptions.Contains(value)))
            {
                return;
            }
            _selectedListOption = value;
            Notify();
            Notify(nameof(CanAddListItem));
        }
    }

    public bool CanAddListItem =>
        (_metadata.ListConstraints?.AllowAdd ?? false)
        && ListValues.Count < (_metadata.ListConstraints?.MaximumItems ?? 0)
        && SelectedListOption is not null;
    public bool CanRemoveListItem =>
        (_metadata.ListConstraints?.AllowRemove ?? false)
        && ListValues.Count > (_metadata.ListConstraints?.MinimumItems ?? 0);
    public bool CanReorderListItems =>
        _metadata.ListConstraints?.AllowReorder ?? false;
    public bool ListMembershipEditable =>
        (_metadata.ListConstraints?.AllowAdd ?? false)
        || (_metadata.ListConstraints?.AllowRemove ?? false);
    public bool ListReorderAvailable =>
        _metadata.ListConstraints?.AllowReorder ?? false;

    public string ListConstraintDisplay
    {
        get
        {
            var constraints = _metadata.ListConstraints;
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

    public JsonElement? CurrentValue
    {
        get
        {
            if (UsesObjectEditor)
            {
                var result = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
                foreach (var field in Fields)
                {
                    if (field.CurrentValue is not { } current)
                    {
                        return null;
                    }
                    result[field.Key] = current.Clone();
                }
                return JsonSerializer.SerializeToElement(result);
            }
            if (UsesListEditor)
            {
                return JsonSerializer.SerializeToElement(
                    ListValues.Select(option => option.Value.Clone()).ToArray());
            }
            return null;
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
        }
    }

    private void FieldChanged()
    {
        RefreshUniqueFieldOptions();
        ValueChanged();
    }

    private void RefreshUniqueFieldOptions()
    {
        if (!_metadata.UniqueFieldValues)
        {
            return;
        }
        var repeatableKeys = _metadata.RepeatableFieldValues
            .Select(value => value.GetRawText())
            .ToHashSet(StringComparer.Ordinal);
        foreach (var field in Fields)
        {
            var selectedByOthers = Fields
                .Where(candidate => !ReferenceEquals(candidate, field))
                .Select(candidate => candidate.SelectedOption?.ValueKey)
                .Where(key => key is not null)
                .Cast<string>()
                .Where(key => !repeatableKeys.Contains(key))
                .ToHashSet(StringComparer.Ordinal);
            field.RefreshAvailableOptions(selectedByOthers);
        }
    }

    private void ListChanged()
    {
        RefreshAvailableListOptions();
        Notify(nameof(CanAddListItem));
        Notify(nameof(CanRemoveListItem));
        ValueChanged();
    }

    private void RefreshAvailableListOptions()
    {
        var selected = ListValues.Select(option => option.ValueKey).ToHashSet(
            StringComparer.Ordinal);
        AvailableListOptions.Clear();
        foreach (var option in _metadata.Options.Where(option =>
                     !selected.Contains(option.ValueKey)))
        {
            AvailableListOptions.Add(option);
        }
        Notify(nameof(AvailableListOptions));
    }

    private void ValueChanged()
    {
        if (!_configuring)
        {
            _changed();
        }
    }

    private void Notify([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}

internal static class EditorJson
{
    public static StrategyEditorOption? FindOption(
        IEnumerable<StrategyEditorOption> options,
        JsonElement? value)
    {
        if (!value.HasValue)
        {
            return null;
        }
        return options.FirstOrDefault(option =>
            ValuesEqual(option.Value, value.Value));
    }

    public static Dictionary<string, JsonElement> ObjectValues(JsonElement? value)
    {
        var result = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        if (!value.HasValue || value.Value.ValueKind != JsonValueKind.Object)
        {
            return result;
        }
        foreach (var property in value.Value.EnumerateObject())
        {
            result[property.Name] = property.Value.Clone();
        }
        return result;
    }

    internal static bool ValuesEqual(JsonElement left, JsonElement right)
    {
        if (left.ValueKind != right.ValueKind)
        {
            return false;
        }
        return left.ValueKind switch
        {
            JsonValueKind.Object => ObjectValues(left).Count == ObjectValues(right).Count
                && ObjectValues(left).All(item =>
                    ObjectValues(right).TryGetValue(item.Key, out var other)
                    && ValuesEqual(item.Value, other)),
            JsonValueKind.Array => left.EnumerateArray().Count()
                == right.EnumerateArray().Count()
                && left.EnumerateArray().Zip(right.EnumerateArray())
                    .All(pair => ValuesEqual(pair.First, pair.Second)),
            JsonValueKind.String => left.GetString() == right.GetString(),
            JsonValueKind.Number => left.GetRawText() == right.GetRawText(),
            JsonValueKind.True or JsonValueKind.False =>
                left.GetBoolean() == right.GetBoolean(),
            JsonValueKind.Null or JsonValueKind.Undefined => true,
            _ => left.GetRawText() == right.GetRawText(),
        };
    }
}
