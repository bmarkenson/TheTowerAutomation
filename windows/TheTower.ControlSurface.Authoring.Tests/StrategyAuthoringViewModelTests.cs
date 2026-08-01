using System.Text.Json;

namespace TheTower.ControlSurface;

public sealed class StrategyAuthoringViewModelTests
{
    public static TheoryData<string> EditorTypes => new()
    {
        "fixed_value",
        "boolean",
        "preset",
        "damage_percentage",
        "card_recharge_modes",
        "ordered_list",
        "perk_multiselect",
        "perk_order",
        "ultimate_weapon_toggles",
    };

    [Theory]
    [MemberData(nameof(EditorTypes))]
    public void EveryEditorBuildsAnOmittedBaseDirectiveFromServerInitialValue(
        string editorType)
    {
        var definition = Definition(editorType);
        var row = Row(definition, isBase: true);

        Assert.False(row.IsReadOnlyValue);
        Assert.Null(row.BuildDirective());

        row.SelectedSourceState = State(row, "included_enforce");
        var directive = Assert.IsType<StrategyAuthoringDirective>(row.BuildDirective());

        Assert.Equal("enforce", directive.Policy);
        AssertJson(definition.InitialValue, directive.Value);
    }

    [Theory]
    [MemberData(nameof(EditorTypes))]
    public void EveryEditorBuildsAnOmittedStrategyOverrideFromServerInitialValue(
        string editorType)
    {
        var definition = Definition(editorType);
        var row = Row(definition, isBase: false);

        Assert.Null(row.BuildDirective());

        row.SelectedSourceState = State(row, "override_observe");
        var directive = Assert.IsType<StrategyAuthoringDirective>(row.BuildDirective());

        Assert.Equal("observe", directive.Policy);
        AssertJson(definition.InitialValue, directive.Value);
    }

    [Theory]
    [MemberData(nameof(EditorTypes))]
    public void EveryEditorSupportsEveryBaseSourceStateTransition(string editorType)
    {
        var definition = Definition(editorType);
        var stateIds = new[]
        {
            "not_included",
            "included_enforce",
            "included_observe",
        };
        foreach (var from in stateIds)
        {
            foreach (var to in stateIds)
            {
                var row = Row(
                    definition,
                    isBase: true,
                    directive: DirectiveForState(from, definition.InitialValue));
                row.SelectedSourceState = State(row, to);

                var result = row.BuildDirective();
                if (to == "not_included")
                {
                    Assert.Null(result);
                }
                else
                {
                    Assert.Equal(
                        to == "included_enforce" ? "enforce" : "observe",
                        result?.Policy);
                    AssertJson(definition.InitialValue, result?.Value);
                }
            }
        }
    }

    [Theory]
    [MemberData(nameof(EditorTypes))]
    public void EveryEditorSupportsEveryStrategySourceStateTransition(
        string editorType)
    {
        var definition = Definition(editorType);
        var stateIds = new[]
        {
            "inherit",
            "override_enforce",
            "override_observe",
            "ignore",
        };
        foreach (var from in stateIds)
        {
            foreach (var to in stateIds)
            {
                var row = Row(
                    definition,
                    isBase: false,
                    directive: DirectiveForState(from, definition.InitialValue));
                row.SelectedSourceState = State(row, to);

                var result = row.BuildDirective();
                if (to == "inherit")
                {
                    Assert.Null(result);
                }
                else if (to == "ignore")
                {
                    Assert.Equal("ignore", result?.Policy);
                    if (from == "inherit")
                    {
                        Assert.Null(result?.Value);
                    }
                    else
                    {
                        AssertJson(definition.InitialValue, result?.Value);
                    }
                }
                else
                {
                    Assert.Equal(
                        to == "override_enforce" ? "enforce" : "observe",
                        result?.Policy);
                    AssertJson(definition.InitialValue, result?.Value);
                }
            }
        }
    }

    [Fact]
    public void CardRechargeEditorSerializesOneServerChoicePerCard()
    {
        var row = ManagedRow(Definition("card_recharge_modes"));

        Assert.Equal(new[] { "Demon Mode", "Nuke" }, row.ChoiceFields.Select(
            field => field.DisplayName));
        row.ChoiceFields[0].SelectedOption = row.ChoiceFields[0].Options.Single(
            option => option.Value.GetString() == "ready_after_recharge");

        AssertJson(
            Element(new Dictionary<string, string>
            {
                ["Demon Mode"] = "ready_after_recharge",
                ["Nuke"] = "ready_after_recharge",
            }),
            row.BuildDirective()?.Value);
    }

    [Fact]
    public void OrderedListActionsFollowTheServerConstraintContract()
    {
        var freeLocks = ManagedRow(Definition("ordered_list"));

        Assert.False(freeLocks.CanAddListItem);
        Assert.False(freeLocks.CanRemoveListItem);
        Assert.True(freeLocks.CanReorderListItems);
        freeLocks.MoveListItem(freeLocks.ListValues[2], -1);
        AssertJson(
            Element(new[] { "Shockwave Size", "Bounce Shot Range", "Bounce Shot Targets" }),
            freeLocks.BuildDirective()?.Value);

        var guardianDefinition = Definition("ordered_list");
        guardianDefinition.InitialValue = Element(new[] { "Fetch", "Summon", "Scout" });
        guardianDefinition.Editor.Fixed = true;
        guardianDefinition.Editor.Options = Options("Fetch", "Summon", "Scout");
        guardianDefinition.Editor.ListConstraints = ExactList(
            new[] { "Fetch", "Summon", "Scout" },
            allowReorder: false,
            orderSignificant: false);
        var guardians = ManagedRow(guardianDefinition);

        Assert.False(guardians.CanAddListItem);
        Assert.False(guardians.CanRemoveListItem);
        Assert.False(guardians.CanReorderListItems);
        guardians.MoveListItem(guardians.ListValues[2], -1);
        AssertJson(guardianDefinition.InitialValue, guardians.BuildDirective()?.Value);
    }

    [Theory]
    [InlineData("perk_multiselect", false)]
    [InlineData("perk_order", true)]
    public void PerkEditorsPreventDuplicatesEnforceLimitsAndRespectOrdering(
        string editorType,
        bool reorderable)
    {
        var row = ManagedRow(Definition(editorType));

        Assert.DoesNotContain(
            row.AvailableListOptions,
            option => option.Value.GetString() == "one");
        row.SelectedListOption = row.AvailableListOptions.Single(
            option => option.Value.GetString() == "two");
        row.AddSelectedListItem();

        Assert.Equal(2, row.ListValues.Count);
        Assert.False(row.CanAddListItem);
        Assert.Equal(reorderable, row.CanReorderListItems);
        row.MoveListItem(row.ListValues[1], -1);
        var expected = reorderable
            ? Element(new[] { "two", "one" })
            : Element(new[] { "one", "two" });
        AssertJson(expected, row.BuildDirective()?.Value);
    }

    [Fact]
    public void UltimateWeaponEditorPreservesUnknownValuesAndConstrainsStun()
    {
        var definition = Definition("ultimate_weapon_toggles");
        var retained = Element(new Dictionary<string, object>
        {
            ["Poison Swamp"] = new Dictionary<string, string>
            {
                ["primary"] = "on",
                ["stun"] = "off",
                ["future_toggle"] = "on",
            },
            ["Future Beam"] = new Dictionary<string, string>
            {
                ["primary"] = "off",
                ["mode"] = "on",
            },
        });
        var row = Row(
            definition,
            isBase: false,
            directive: new StrategyAuthoringDirective
            {
                Policy = "enforce",
                Value = retained,
            });
        var poison = Assert.Single(row.UltimateGroups);
        var primary = poison.Fields.Single(field => field.Key == "primary");
        var stun = poison.Fields.Single(field => field.Key == "stun");

        Assert.Single(stun.Options);
        Assert.False(stun.SelectionEnabled);
        stun.SelectedOption = Option("on");
        Assert.Equal("off", stun.SelectedOption?.Value.GetString());
        primary.SelectedOption = primary.Options.Single(
            option => option.Value.GetString() == "off");

        AssertJson(
            Element(new Dictionary<string, object>
            {
                ["Poison Swamp"] = new Dictionary<string, string>
                {
                    ["primary"] = "off",
                    ["stun"] = "off",
                    ["future_toggle"] = "on",
                },
                ["Future Beam"] = new Dictionary<string, string>
                {
                    ["primary"] = "off",
                    ["mode"] = "on",
                },
            }),
            row.BuildDirective()?.Value);
    }

    [Fact]
    public void UltimateWeaponMinimumsPreventAnEmptyManagedValue()
    {
        var row = ManagedRow(Definition("ultimate_weapon_toggles"));
        var poison = Assert.Single(row.UltimateGroups);
        var primary = poison.Fields.Single(field => field.Key == "primary");
        var stun = poison.Fields.Single(field => field.Key == "stun");

        poison.IsIncluded = false;
        Assert.True(poison.IsIncluded);

        primary.IsIncluded = false;
        Assert.False(primary.IsIncluded);
        stun.IsIncluded = false;
        Assert.True(stun.IsIncluded);
        Assert.True(poison.IsEffectivelyPresent);
    }

    [Fact]
    public void FixedAndBooleanEditorsCannotCreateUnsupportedDraftValues()
    {
        var fixedRow = ManagedRow(Definition("fixed_value"));
        var booleanRow = ManagedRow(Definition("boolean"));

        booleanRow.BooleanValue = false;

        Assert.Equal("Farm", fixedRow.BuildDirective()?.Value?.GetString());
        Assert.True(booleanRow.BuildDirective()?.Value?.GetBoolean());
        Assert.False(booleanRow.BooleanControlEnabled);
    }

    [Fact]
    public void PresetsAreLimitedToServerOptionsAndDamageStaysServerValidatedText()
    {
        var preset = ManagedRow(Definition("preset"));
        var original = preset.SelectedPreset;
        preset.SelectedPreset = Option("client-invented");
        Assert.Same(original, preset.SelectedPreset);
        preset.SelectedPreset = preset.PresetOptions.Single(
            option => option.Value.GetString() == "second");
        AssertJson(
            Element(new Dictionary<string, string> { ["preset"] = "second" }),
            preset.BuildDirective()?.Value);

        var damage = ManagedRow(Definition("damage_percentage"));
        damage.ValueText = "2e-19%";
        Assert.Equal("2e-19%", damage.BuildDirective()?.Value?.GetString());
    }

    [Fact]
    public void DormantIgnoreValueSurvivesInheritedAndReconstructedRows()
    {
        var definition = Definition("ultimate_weapon_toggles");
        var row = ManagedRow(definition);
        var poison = Assert.Single(row.UltimateGroups);
        poison.Fields.Single(field => field.Key == "primary").SelectedOption =
            poison.Fields.Single(field => field.Key == "primary").Options.Single(
                option => option.Value.GetString() == "off");
        row.SelectedSourceState = State(row, "ignore");
        var ignored = Assert.IsType<StrategyAuthoringDirective>(row.BuildDirective());
        var dormant = row.CaptureDormantValue();

        row.SelectedSourceState = State(row, "inherit");
        Assert.Null(row.BuildDirective());
        var reopened = Row(
            definition,
            isBase: false,
            directive: null,
            dormantValue: dormant);
        reopened.SelectedSourceState = State(reopened, "ignore");

        AssertJson(ignored.Value, reopened.BuildDirective()?.Value);
    }

    [Theory]
    [InlineData(nameof(AuthoringSettingRowViewModel.EffectivePolicyDisplay))]
    [InlineData(nameof(AuthoringSettingRowViewModel.EffectiveValueDisplay))]
    [InlineData(nameof(AuthoringSettingRowViewModel.ProvenanceDisplay))]
    [InlineData(nameof(AuthoringSettingRowViewModel.PendingEffectiveDisplay))]
    public void ComputedDisplayPropertiesRemainReadOnly(string propertyName)
    {
        var property = typeof(AuthoringSettingRowViewModel).GetProperty(propertyName);

        Assert.NotNull(property);
        Assert.False(property!.CanWrite);
    }

    [Fact]
    public void ServerMetadataModelsRoundTripForEveryEditorFamily()
    {
        foreach (var editorType in EditorTypes)
        {
            var payload = JsonSerializer.Serialize(Definition(editorType));
            var definition = JsonSerializer.Deserialize<StrategySettingDefinition>(
                payload);

            Assert.Contains("\"initial_value\"", payload);
            Assert.Contains("\"editor\"", payload);
            Assert.NotNull(definition);
            var row = Row(definition!, isBase: false);
            row.SelectedSourceState = State(row, "override_enforce");
            AssertJson(definition.InitialValue, row.BuildDirective()?.Value);
        }
    }

    private static AuthoringSettingRowViewModel ManagedRow(
        StrategySettingDefinition definition)
    {
        var row = Row(definition, isBase: false);
        row.SelectedSourceState = State(row, "override_enforce");
        return row;
    }

    private static AuthoringSettingRowViewModel Row(
        StrategySettingDefinition definition,
        bool isBase,
        StrategyAuthoringDirective? directive = null,
        AuthoringDormantValue? dormantValue = null) => new(
            definition,
            isBase,
            entityEditable: true,
            directive,
            resolution: null,
            Capabilities(),
            dormantValue);

    private static AuthoringSourceStateDefinition State(
        AuthoringSettingRowViewModel row,
        string id) => row.AvailableSourceStates.Single(state => state.Id == id);

    private static StrategyAuthoringDirective? DirectiveForState(
        string stateId,
        JsonElement? value) => stateId switch
        {
            "not_included" or "inherit" => null,
            "included_enforce" or "override_enforce" => new()
            {
                Policy = "enforce",
                Value = value?.Clone(),
            },
            "included_observe" or "override_observe" => new()
            {
                Policy = "observe",
                Value = value?.Clone(),
            },
            "ignore" => new()
            {
                Policy = "ignore",
                Value = value?.Clone(),
            },
            _ => throw new ArgumentOutOfRangeException(nameof(stateId)),
        };

    private static StrategyAuthoringCapabilities Capabilities() => new()
    {
        BaseSourceStates =
        [
            new() { Id = "not_included", DisplayName = "Not included" },
            new() { Id = "included_enforce", DisplayName = "Enforce", Policy = "enforce" },
            new() { Id = "included_observe", DisplayName = "Observe", Policy = "observe" },
        ],
        StrategySourceStates =
        [
            new() { Id = "inherit", DisplayName = "Inherited" },
            new() { Id = "override_enforce", DisplayName = "Enforce", Policy = "enforce" },
            new() { Id = "override_observe", DisplayName = "Observe", Policy = "observe" },
            new() { Id = "ignore", DisplayName = "Ignore", Policy = "ignore" },
        ],
    };

    private static StrategySettingDefinition Definition(string editorType)
    {
        var definition = new StrategySettingDefinition
        {
            Id = editorType,
            DisplayName = editorType,
            EditorType = editorType,
            AllowedPolicies = ["enforce", "observe", "ignore"],
        };
        switch (editorType)
        {
            case "fixed_value":
                definition.InitialValue = Element("Farm");
                definition.Editor = Metadata(
                    fixedValue: true,
                    options: Options("Farm"));
                break;
            case "boolean":
                definition.InitialValue = Element(true);
                definition.Editor = Metadata(
                    fixedValue: true,
                    options: [Option(true, "Enabled")]);
                break;
            case "preset":
                definition.InitialValue = Element(
                    new Dictionary<string, string> { ["preset"] = "first" });
                definition.Editor = Metadata(
                    fields:
                    [
                        Field("preset", "first", Options("first", "second")),
                    ]);
                break;
            case "damage_percentage":
                definition.InitialValue = Element("1E-22%");
                definition.Editor = Metadata();
                definition.Editor.ServerNormalizedText = true;
                break;
            case "card_recharge_modes":
                definition.InitialValue = Element(new Dictionary<string, string>
                {
                    ["Demon Mode"] = "auto_reactivate",
                    ["Nuke"] = "ready_after_recharge",
                });
                var rechargeOptions = Options(
                    "auto_reactivate",
                    "ready_after_recharge");
                definition.Editor = Metadata(
                    fields:
                    [
                        Field("Demon Mode", "auto_reactivate", rechargeOptions),
                        Field("Nuke", "ready_after_recharge", rechargeOptions),
                    ]);
                break;
            case "ordered_list":
                var locks = new[]
                {
                    "Shockwave Size",
                    "Bounce Shot Targets",
                    "Bounce Shot Range",
                };
                definition.InitialValue = Element(locks);
                definition.Editor = Metadata(options: Options(locks));
                definition.Editor.ListConstraints = ExactList(
                    locks,
                    allowReorder: true,
                    orderSignificant: true);
                break;
            case "perk_multiselect":
            case "perk_order":
                definition.InitialValue = Element(new[] { "one" });
                definition.Editor = Metadata(options: Options("one", "two", "three"));
                definition.Editor.ListConstraints = new StrategyListConstraints
                {
                    MinimumItems = editorType == "perk_order" ? 1 : 0,
                    MaximumItems = 2,
                    UniqueItems = true,
                    AllowAdd = true,
                    AllowRemove = true,
                    AllowReorder = editorType == "perk_order",
                    OrderSignificant = editorType == "perk_order",
                };
                break;
            case "ultimate_weapon_toggles":
                definition.InitialValue = Element(new Dictionary<string, object>
                {
                    ["Poison Swamp"] = new Dictionary<string, string>
                    {
                        ["primary"] = "on",
                        ["stun"] = "off",
                    },
                });
                definition.Editor = Metadata();
                definition.Editor.PreserveUnknownFields = true;
                definition.Editor.AllowGroupSelection = true;
                definition.Editor.MinimumSelectedGroups = 1;
                definition.Editor.Groups =
                [
                    new StrategyEditorGroup
                    {
                        Key = "Poison Swamp",
                        DisplayName = "Poison Swamp",
                        InitiallyIncluded = true,
                        AllowSelection = true,
                        MinimumSelectedFields = 1,
                        PreserveUnknownFields = true,
                        Fields =
                        [
                            Field("primary", "on", Options("on", "off")),
                            Field("stun", "off", Options("off"), fixedValue: true),
                        ],
                    },
                ];
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(editorType));
        }
        return definition;
    }

    private static StrategyEditorMetadata Metadata(
        bool fixedValue = false,
        List<StrategyEditorOption>? options = null,
        List<StrategyEditorField>? fields = null) => new()
        {
            SchemaVersion = 1,
            Fixed = fixedValue,
            Options = options ?? [],
            Fields = fields ?? [],
        };

    private static StrategyEditorField Field(
        string key,
        object initial,
        List<StrategyEditorOption> options,
        bool fixedValue = false) => new()
        {
            Key = key,
            DisplayName = key,
            InitialValue = Element(initial),
            Options = options,
            Fixed = fixedValue,
        };

    private static StrategyListConstraints ExactList(
        IEnumerable<string> values,
        bool allowReorder,
        bool orderSignificant)
    {
        var items = values.ToList();
        return new StrategyListConstraints
        {
            MinimumItems = items.Count,
            MaximumItems = items.Count,
            UniqueItems = true,
            AllowReorder = allowReorder,
            OrderSignificant = orderSignificant,
            ExactItems = items,
        };
    }

    private static List<StrategyEditorOption> Options(params string[] values) =>
        values.Select(value => Option(value)).ToList();

    private static StrategyEditorOption Option(object value, string? display = null) =>
        new()
        {
            Value = Element(value),
            DisplayName = display ?? value.ToString() ?? "",
        };

    private static JsonElement Element<T>(T value) =>
        JsonSerializer.SerializeToElement(value);

    private static void AssertJson(JsonElement? expected, JsonElement? actual)
    {
        Assert.True(expected.HasValue, "Expected JSON was absent.");
        Assert.True(actual.HasValue, "Actual JSON was absent.");
        Assert.Equal(Canonical(expected.Value), Canonical(actual.Value));
    }

    private static string Canonical(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Object => "{" + string.Join(
            ",",
            value.EnumerateObject()
                .OrderBy(property => property.Name, StringComparer.Ordinal)
                .Select(property => JsonSerializer.Serialize(property.Name)
                    + ":" + Canonical(property.Value))) + "}",
        JsonValueKind.Array => "[" + string.Join(
            ",",
            value.EnumerateArray().Select(Canonical)) + "]",
        _ => value.GetRawText(),
    };
}
