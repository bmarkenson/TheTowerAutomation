using System.ComponentModel;
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

    private static bool ValuesEqual(JsonElement left, JsonElement right)
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
