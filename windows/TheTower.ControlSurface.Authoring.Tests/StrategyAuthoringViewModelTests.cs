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

    public static TheoryData<string> LocalEditorKinds => new()
    {
        "modules",
        "target_priority",
        "orb_distance",
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

    [Theory]
    [MemberData(nameof(LocalEditorKinds))]
    public void PresetAndLocalDraftsSurviveFormAndSourceStateTransitions(
        string kind)
    {
        var definition = LocalDefinition(kind);
        var row = ManagedRow(definition);
        var presetForm = row.DefinitionForms[0];
        var localForm = row.DefinitionForms[1];

        row.SelectedDefinitionForm = localForm;
        MutateLocalDefinition(row, kind);
        var localDraft = row.BuildDirective()?.Value;

        row.SelectedDefinitionForm = presetForm;
        row.SelectedPreset = row.PresetOptions[1];
        var presetDraft = row.BuildDirective()?.Value;
        row.SelectedSourceState = State(row, "ignore");
        AssertJson(presetDraft, row.BuildDirective()?.Value);
        row.SelectedSourceState = State(row, "inherit");
        Assert.Null(row.BuildDirective());

        var dormant = row.CaptureDormantValue();
        var reopened = Row(
            definition,
            isBase: false,
            directive: null,
            dormantValue: dormant);
        reopened.SelectedSourceState = State(reopened, "override_observe");
        AssertJson(presetDraft, reopened.BuildDirective()?.Value);
        reopened.SelectedDefinitionForm = reopened.DefinitionForms[1];
        AssertJson(localDraft, reopened.BuildDirective()?.Value);
        reopened.SelectedSourceState = State(reopened, "ignore");
        AssertJson(localDraft, reopened.BuildDirective()?.Value);
    }

    [Theory]
    [MemberData(nameof(LocalEditorKinds))]
    public void PresetAndLocalDraftsSurviveSparseBaseTransitions(string kind)
    {
        var definition = LocalDefinition(kind);
        var row = Row(definition, isBase: true);
        row.SelectedSourceState = State(row, "included_enforce");
        row.SelectedDefinitionForm = row.DefinitionForms[1];
        MutateLocalDefinition(row, kind);
        var localDraft = row.BuildDirective()?.Value;

        row.SelectedSourceState = State(row, "not_included");
        Assert.Null(row.BuildDirective());
        var dormant = row.CaptureDormantValue();
        var reopened = Row(
            definition,
            isBase: true,
            directive: null,
            dormantValue: dormant);
        reopened.SelectedSourceState = State(reopened, "included_observe");

        AssertJson(localDraft, reopened.BuildDirective()?.Value);
        reopened.SelectedDefinitionForm = reopened.DefinitionForms[0];
        AssertJson(definition.InitialValue, reopened.BuildDirective()?.Value);
    }

    [Fact]
    public void ModuleLocalEditorUsesEightFieldsAndPreventsRepeatedChoices()
    {
        var row = ManagedRow(LocalDefinition("modules"));
        row.SelectedDefinitionForm = row.DefinitionForms[1];
        var local = Assert.IsType<AuthoringLocalDefinitionViewModel>(
            row.LocalDefinitionEditor);

        Assert.Equal(8, local.Fields.Count);
        var first = local.Fields[0];
        var second = local.Fields[1];
        var repeated = first.Definition.Options.Single(option =>
            option.Value.GetString() == second.SelectedOption?.Value.GetString());
        Assert.DoesNotContain(
            first.AvailableOptions,
            option => option.Value.GetString() == repeated.Value.GetString());

        var original = first.SelectedOption;
        first.SelectedOption = repeated;
        Assert.Same(original, first.SelectedOption);
        first.SelectedOption = first.AvailableOptions.Single(option =>
            option.Value.GetString()?.EndsWith("3", StringComparison.Ordinal) == true);

        var definition = row.BuildDirective()?.Value?.GetProperty("local");
        Assert.True(definition.HasValue);
        var values = definition.Value.EnumerateObject()
            .Select(property => property.Value.GetString())
            .ToArray();
        Assert.Equal(8, values.Length);
        Assert.Equal(8, values.Distinct(StringComparer.Ordinal).Count());
    }

    [Fact]
    public void TargetPriorityLocalEditorRetainsCompleteMembershipInChangedOrder()
    {
        var definition = LocalDefinition("target_priority");
        var row = ManagedRow(definition);
        row.SelectedDefinitionForm = row.DefinitionForms[1];
        var local = Assert.IsType<AuthoringLocalDefinitionViewModel>(
            row.LocalDefinitionEditor);
        var before = local.ListValues.Select(option => option.Value.GetString()).ToArray();

        Assert.Equal(10, local.ListValues.Count);
        Assert.False(local.CanAddListItem);
        Assert.False(local.CanRemoveListItem);
        Assert.True(local.CanReorderListItems);
        local.MoveListItem(local.ListValues[^1], -1);

        var after = row.BuildDirective()?.Value?.GetProperty("local")
            .EnumerateArray()
            .Select(item => item.GetString())
            .ToArray();
        Assert.NotNull(after);
        Assert.Equal(before.Order(StringComparer.Ordinal), after!.Order(StringComparer.Ordinal));
        Assert.NotEqual(before, after);
    }

    [Fact]
    public void OrbLocalEditorEmitsExactlyThreeUnnormalizedTextFields()
    {
        var row = ManagedRow(LocalDefinition("orb_distance"));
        row.SelectedDefinitionForm = row.DefinitionForms[1];
        var local = Assert.IsType<AuthoringLocalDefinitionViewModel>(
            row.LocalDefinitionEditor);

        Assert.Equal(3, local.Fields.Count);
        Assert.All(local.Fields, field => Assert.True(field.UsesTextEditor));
        local.Fields[0].ValueText = "not a distance";

        var value = row.BuildDirective()?.Value?.GetProperty("local");
        Assert.True(value.HasValue);
        Assert.Equal(
            new[] { "range_basis", "extra", "workshop" },
            value.Value.EnumerateObject().Select(property => property.Name));
        Assert.Equal("not a distance", value.Value.GetProperty("range_basis").GetString());
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
    public void BasePinReviewCoversFirstAttachmentWithoutImplyingActivation()
    {
        var review = StrategyAuthoringReviewFormatter.FormatRebaseReview(
            new StrategyAuthoringMutationResponse
            {
                Rebase = new StrategyRebasePreview(),
            });

        Assert.Contains("BASE PIN REVIEW", review);
        Assert.Contains("draft's pinned Base reference", review);
        Assert.Contains("publishing will not activate it", review);
    }

    [Fact]
    public void HistoryRevisionReviewShowsImmutableIdentityAndValidationState()
    {
        var review = StrategyHistoryReviewFormatter.FormatRevision(
            new StrategyRevisionSummary
            {
                StrategyId = "night-farm",
                DisplayName = "Night Farm",
                LogicalVersion = 4,
                Status = "historical",
                PublishedAt = "2026-08-02T12:00:00Z",
                Family = "farming",
                Tier = 11,
                PinnedBaseId = "farm-base",
                PinnedBaseRevision = 3,
                PublicationOrigin = "restore_as_new",
                AuditIdentity = new StrategyAuditIdentity
                {
                    Authority = "control_surface",
                    EventId = "evt-4",
                },
                PublicationSchemaVersion = 2,
                RuleCount = 17,
                SourceFingerprint = "source",
                NormalizedSourceFingerprint = "normalized",
                BaseFingerprint = "base",
                ResolutionFingerprint = "resolution",
                PlanFingerprint = "plan",
                PublicationFingerprint = "publication",
                RevisionFingerprint = "revision",
                CurrentValidationValid = false,
                ValidationErrors =
                [
                    new AuthoringValidationError { Message = "Current builder rejected it." },
                ],
                Warnings = ["Retained safely for review."],
            });

        Assert.Contains("Logical version: 4 — historical", review);
        Assert.Contains("Pinned Base: farm-base@3", review);
        Assert.Contains("Origin: restore_as_new", review);
        Assert.Contains("control_surface / evt-4", review);
        Assert.Contains("Plan: plan", review);
        Assert.Contains("Current builder rejected it.", review);
        Assert.Contains("Retained safely for review.", review);
    }

    [Fact]
    public void RestoreReviewExplainsSemanticChangesAndNeverImpliesActivation()
    {
        var review = StrategyHistoryReviewFormatter.FormatComparison(
            new StrategyRevisionSummary { LogicalVersion = 2 },
            new StrategyAuthoringMutationResponse
            {
                NextLogicalVersion = 6,
                Comparison = new StrategyRevisionComparison
                {
                    SourceChanges = new AuthoringSourceDiff
                    {
                        Added =
                        [
                            new AuthoringDiffItem { DisplayName = "Free Upgrades" },
                        ],
                    },
                    EffectiveChanges = new AuthoringResolutionDiff
                    {
                        ChangeCount = 2,
                        ProvenanceChanged = [new AuthoringResolutionChange()],
                    },
                    BaseSnapshotChanges = new StrategyBaseSnapshotDiff
                    {
                        Changed = true,
                        BeforeReference = new StrategyBaseReference
                        {
                            Id = "farm-base",
                            Revision = 1,
                        },
                        AfterReference = new StrategyBaseReference
                        {
                            Id = "farm-base",
                            Revision = 3,
                        },
                    },
                    LocalOverrideChanges = new StrategyDirectiveDiff
                    {
                        ChangeCount = 1,
                    },
                    ExplicitIgnoreChanges = new StrategyDirectiveDiff
                    {
                        ChangeCount = 1,
                    },
                    GeneratedPlanChanges = new StrategyGeneratedPlanDiff
                    {
                        Changed = true,
                        BeforeRuleCount = 12,
                        AfterRuleCount = 14,
                        RuleCountChange = 2,
                    },
                    Validation = new AuthoringValidationResult { Valid = true },
                },
            });

        Assert.Contains("RESTORE-AS-NEW REVIEW", review);
        Assert.Contains("version 2; proposed new latest version 6", review);
        Assert.Contains("Added: Free Upgrades", review);
        Assert.Contains("farm-base@1 → farm-base@3", review);
        Assert.Contains("explicit Ignore changes: 1", review);
        Assert.Contains("rules 12 → 14 (+2)", review);
        Assert.Contains("Current trusted validation: passed", review);
        Assert.Contains("does not mutate the selected revision", review);
        Assert.Contains("select or activate", review);
        Assert.Contains("alter Pause/control state", review);
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

    private static StrategySettingDefinition LocalDefinition(string kind)
    {
        var definition = new StrategySettingDefinition
        {
            Id = kind,
            DisplayName = kind,
            EditorType = "preset",
            AllowedPolicies = ["enforce", "observe", "ignore"],
            InitialValue = Element(
                new Dictionary<string, string> { ["preset"] = "shared_one" }),
            Editor = Metadata(
                fields:
                [
                    Field(
                        "preset",
                        "shared_one",
                        Options("shared_one", "shared_two")),
                ]),
        };
        definition.Editor.ValueKind = "object";
        definition.Editor.LocalEditor = kind switch
        {
            "modules" => ModuleLocalMetadata(),
            "target_priority" => TargetPriorityLocalMetadata(),
            "orb_distance" => OrbDistanceLocalMetadata(),
            _ => throw new ArgumentOutOfRangeException(nameof(kind)),
        };
        return definition;
    }

    private static StrategyEditorMetadata ModuleLocalMetadata()
    {
        var fields = new List<StrategyEditorField>();
        var initial = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 0; index < 8; index++)
        {
            var family = index / 2;
            var selected = $"family_{family}_module_{index % 2 + 1}";
            initial[$"slot_{index + 1}"] = selected;
            fields.Add(
                Field(
                    $"slot_{index + 1}",
                    selected,
                    Options(
                        $"family_{family}_module_1",
                        $"family_{family}_module_2",
                        $"family_{family}_module_3")));
        }
        return new StrategyEditorMetadata
        {
            SchemaVersion = 1,
            Key = "local",
            DisplayName = "Profile-local definition",
            ValueKind = "object",
            HelpText = "Choose every slot.",
            InitialValue = Element(initial),
            UniqueFieldValues = true,
            Fields = fields,
        };
    }

    private static StrategyEditorMetadata TargetPriorityLocalMetadata()
    {
        var targets = Enumerable.Range(1, 10)
            .Select(index => $"target_{index}")
            .ToArray();
        return new StrategyEditorMetadata
        {
            SchemaVersion = 1,
            Key = "local",
            DisplayName = "Profile-local definition",
            ValueKind = "array",
            HelpText = "Order every target.",
            InitialValue = Element(targets),
            Options = Options(targets),
            ListConstraints = ExactList(
                targets,
                allowReorder: true,
                orderSignificant: true),
        };
    }

    private static StrategyEditorMetadata OrbDistanceLocalMetadata()
    {
        var initial = new Dictionary<string, string>
        {
            ["range_basis"] = "30.00m",
            ["extra"] = "30.00m",
            ["workshop"] = "39.00m",
        };
        return new StrategyEditorMetadata
        {
            SchemaVersion = 1,
            Key = "local",
            DisplayName = "Profile-local definition",
            ValueKind = "object",
            HelpText = "Linux normalizes these values.",
            InitialValue = Element(initial),
            ServerNormalizedText = true,
            Fields = initial.Select(item => new StrategyEditorField
            {
                Key = item.Key,
                DisplayName = item.Key,
                Required = true,
                InitialValue = Element(item.Value),
            }).ToList(),
        };
    }

    private static void MutateLocalDefinition(
        AuthoringSettingRowViewModel row,
        string kind)
    {
        var local = Assert.IsType<AuthoringLocalDefinitionViewModel>(
            row.LocalDefinitionEditor);
        switch (kind)
        {
            case "modules":
                local.Fields[0].SelectedOption = local.Fields[0].AvailableOptions.Single(
                    option => option.Value.GetString()?.EndsWith(
                        "3",
                        StringComparison.Ordinal) == true);
                break;
            case "target_priority":
                local.MoveListItem(local.ListValues[^1], -1);
                break;
            case "orb_distance":
                local.Fields[0].ValueText = "31m";
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(kind));
        }
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
