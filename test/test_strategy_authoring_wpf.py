from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WPF = ROOT / "windows" / "TheTower.ControlSurface"
WPF_TESTS = ROOT / "windows" / "TheTower.ControlSurface.Authoring.Tests"


def _text(name: str) -> str:
    return (WPF / name).read_text(encoding="utf-8")


def test_wpf_authoring_shell_groups_catalogs_and_registry_sections():
    xaml = _text("StrategyProfilesWindow.xaml")
    code = _text("StrategyProfilesWindow.xaml.cs")
    models = _text("Models.cs")

    assert 'Text="BASES"' in xaml
    assert 'x:Name="BasesList"' in xaml
    assert 'Text="STRATEGIES"' in xaml
    assert 'x:Name="StrategiesList"' in xaml
    assert 'x:Name="BasePinBox"' in xaml
    assert "BasePinBox.IsEnabled = editable && (_isNew || canChooseFirstBase)" in code
    assert "_publishedBasePin = item.Source?.Base is not null" in code
    assert "Choose the first compatible Base" in code
    assert "LatestCompatibleBaseRevisions" in code
    assert (
        "PropertyGroupDescription(nameof(AuthoringSettingRowViewModel.Section))"
        in code
    )
    assert 'x:Name="SettingsList"' in xaml
    assert 'Text="{Binding CapabilityDisplay, Mode=OneWay}"' in xaml
    assert 'Text="{Binding ProvenanceDisplay, Mode=OneWay}"' in xaml
    assert 'Text="{Binding EffectivePolicyDisplay, Mode=OneWay}"' in xaml
    assert 'Text="{Binding EffectiveValueDisplay, Mode=OneWay}"' in xaml
    assert 'JsonPropertyName("observation_supported")' in models
    assert 'JsonPropertyName("repair_supported")' in models
    assert 'JsonPropertyName("initial_value")' in models
    assert 'JsonPropertyName("editor")' in models
    assert 'public StrategyAuthoringResolution Resolution { get; set; }' in models
    assert "resolution: item.Resolution" in code
    assert "Bases are never activatable" in xaml
    assert "Activate" not in xaml


def test_wpf_rows_cover_every_registered_editor_family_without_raw_json():
    xaml = _text("StrategyProfilesWindow.xaml")
    view_models = _text("StrategyAuthoringViewModels.cs")
    structured = _text("StrategyStructuredEditorViewModels.cs")

    expected_checks = {
        'EditorType == "fixed_value"',
        'EditorType == "boolean"',
        'EditorType == "preset"',
        'EditorType == "damage_percentage"',
        'EditorType == "card_recharge_modes"',
        '"ordered_list" or "perk_multiselect" or "perk_order"',
        'EditorType == "ultimate_weapon_toggles"',
    }
    assert all(check in view_models for check in expected_checks)
    for binding in (
        "UsesFixedValueEditor",
        "UsesPresetEditor",
        "UsesPresetOrLocalEditor",
        "UsesBooleanEditor",
        "UsesTextEditor",
        "UsesKeyedChoiceEditor",
        "UsesListEditor",
        "UsesUltimateWeaponEditor",
    ):
        assert f"{{Binding {binding}, Mode=OneWay" in xaml
    assert 'ItemsSource="{Binding ChoiceFields, Mode=OneWay}"' in xaml
    assert 'ItemsSource="{Binding ListValues, Mode=OneWay}"' in xaml
    assert 'ItemsSource="{Binding UltimateGroups, Mode=OneWay}"' in xaml
    assert 'ItemsSource="{Binding DefinitionForms, Mode=OneWay}"' in xaml
    assert (
        'ItemsSource="{Binding LocalDefinitionEditor.Fields, Mode=OneWay}"'
        in xaml
    )
    assert (
        'ItemsSource="{Binding LocalDefinitionEditor.ListValues, Mode=OneWay}"'
        in xaml
    )
    assert "AddSelectedListItem" in view_models
    assert "RemoveListItem" in view_models
    assert "MoveListItem" in view_models
    assert "preserved value" in view_models
    assert "Retained unrecognized weapons" in view_models
    assert "Retained fields" in structured
    assert "complex value is read-only" not in xaml.lower()
    assert "phase has no safe value editor" not in view_models.lower()
    assert "raw json" not in xaml.lower()


def test_wpf_constraints_and_dormant_values_are_metadata_driven():
    xaml = _text("StrategyProfilesWindow.xaml")
    code = _text("StrategyProfilesWindow.xaml.cs")
    view_models = _text("StrategyAuthoringViewModels.cs")
    models = _text("Models.cs")

    for metadata_property in (
        'JsonPropertyName("options")',
        'JsonPropertyName("fields")',
        'JsonPropertyName("list_constraints")',
        'JsonPropertyName("groups")',
        'JsonPropertyName("minimum_selected_groups")',
        'JsonPropertyName("preserve_unknown_fields")',
        'JsonPropertyName("unique_field_values")',
        'JsonPropertyName("local_editor")',
    ):
        assert metadata_property in models
    assert "definition.InitialValue" in view_models
    assert "_definition.Editor.ListConstraints" in view_models
    assert "_definition.Editor.Groups" in view_models
    assert "_definition.Editor.Options" in view_models
    assert "CaptureDormantValue" in view_models
    assert "CaptureDormantValues" in code
    assert "dormantValues" in code
    assert "_hasDormantValue ? CurrentValue()?.Clone() : null" in view_models
    assert 'IsEnabled="{Binding BooleanControlEnabled, Mode=OneWay}"' in xaml
    assert 'IsEnabled="{Binding CanAddListItem, Mode=OneWay}"' in xaml
    assert 'IsEnabled="{Binding CanRemoveListItem, Mode=OneWay}"' in xaml
    assert 'IsEnabled="{Binding CanReorderListItems, Mode=OneWay}"' in xaml


def test_wpf_authoring_code_does_not_hardcode_setting_specific_contracts():
    production = "\n".join(
        _text(name)
        for name in (
            "StrategyAuthoringViewModels.cs",
            "StrategyStructuredEditorViewModels.cs",
            "StrategyProfilesWindow.xaml",
            "StrategyProfilesWindow.xaml.cs",
        )
    )

    for server_owned_value in (
        "cards_deck",
        "free_upgrade_locks",
        "guardian_chips",
        "auto_pick_perks",
        "perk_bans",
        "perk_auto_pick_order",
        "ultimate_weapons",
        "Poison Swamp",
        "Demon Mode",
        "farm_standard",
        "target_priority",
        "orb_distance",
        "range_basis",
        "cannon_primary",
        "Being Annihilator",
    ):
        assert server_owned_value not in production
    assert "_catalog.EditorOptions" not in production


def test_computed_display_bindings_are_explicitly_one_way_regression():
    xaml = _text("StrategyProfilesWindow.xaml")
    view_models = _text("StrategyAuthoringViewModels.cs")

    for property_name in (
        "EffectivePolicyDisplay",
        "EffectiveValueDisplay",
        "ProvenanceDisplay",
        "PendingEffectiveDisplay",
        "CapabilityDisplay",
        "FixedValueDisplay",
        "ListConstraintDisplay",
        "UnknownRetainedDisplay",
        "ValueEditorExplanation",
    ):
        assert f"{{Binding {property_name}, Mode=OneWay}}" in xaml
    assert '<Run Text="{Binding EffectivePolicyDisplay, Mode=OneWay}"' in xaml
    assert '<Run Text="{Binding EffectiveValueDisplay, Mode=OneWay}"' in xaml
    assert 'Text="{Binding EffectivePolicyDisplay}"' not in xaml
    assert 'Text="{Binding EffectiveValueDisplay}"' not in xaml
    assert "public string EffectivePolicyDisplay =>" in view_models
    assert "public string EffectiveValueDisplay =>" in view_models
    assert "public string ProvenanceDisplay =>" in view_models
    assert "public string PendingEffectiveDisplay =>" in view_models


def test_native_combo_theme_keeps_closed_and_dropdown_text_high_contrast():
    app_xaml = _text("App.xaml")
    combo_style = app_xaml.split('<Style TargetType="ComboBox">', 1)[1].split(
        "</Style>", 1
    )[0]
    item_style = app_xaml.split('<Style TargetType="ComboBoxItem">', 1)[1].split(
        "</Style>", 1
    )[0]

    assert '<Setter Property="Background" Value="#182338" />' in combo_style
    assert '<Setter Property="Foreground" Value="#EDF2F7" />' in combo_style
    assert '<Setter Property="Foreground" Value="#111827" />' not in combo_style
    assert '<Setter Property="OverridesDefaultStyle" Value="True" />' in combo_style
    assert '<ControlTemplate TargetType="{x:Type ComboBox}">' in combo_style
    assert 'x:Name="ComboChrome"' in combo_style
    assert 'Background="{TemplateBinding Background}"' in combo_style
    assert 'TextElement.Foreground="{TemplateBinding Foreground}"' in combo_style
    assert 'x:Name="PART_Popup"' in combo_style
    assert 'x:Name="PopupChrome"' in combo_style
    assert '<Trigger Property="IsEnabled" Value="False">' in combo_style
    assert '<Setter Property="Background" Value="#182338" />' in item_style
    assert '<Setter Property="Foreground" Value="#EDF2F7" />' in item_style
    assert '<Setter Property="OverridesDefaultStyle" Value="True" />' in item_style
    assert '<ControlTemplate TargetType="{x:Type ComboBoxItem}">' in item_style
    assert 'x:Name="ItemChrome"' in item_style
    assert 'TextElement.Foreground="{TemplateBinding Foreground}"' in item_style
    assert '<Trigger Property="IsHighlighted" Value="True">' in item_style
    assert '<Trigger Property="IsSelected" Value="True">' in item_style


def test_native_choice_labels_remain_readable_when_editor_is_disabled():
    app_xaml = _text("App.xaml")

    for control_type in ("RadioButton", "CheckBox"):
        style = app_xaml.split(f'<Style TargetType="{control_type}">', 1)[1].split(
            "</Style>", 1
        )[0]
        assert '<Setter Property="Foreground" Value="#EDF2F7" />' in style
        assert '<Trigger Property="IsEnabled" Value="False">' in style
        disabled = style.split(
            '<Trigger Property="IsEnabled" Value="False">', 1
        )[1].split("</Trigger>", 1)[0]
        assert '<Setter Property="Foreground" Value="#7890AC" />' in disabled


def test_portable_view_model_suite_covers_editors_states_and_round_trips():
    project = (WPF_TESTS / "TheTower.ControlSurface.Authoring.Tests.csproj").read_text(
        encoding="utf-8"
    )
    tests = (WPF_TESTS / "StrategyAuthoringViewModelTests.cs").read_text(
        encoding="utf-8"
    )

    assert "StrategyAuthoringViewModels.cs" in project
    assert "StrategyStructuredEditorViewModels.cs" in project
    assert "EveryEditorSupportsEveryBaseSourceStateTransition" in tests
    assert "EveryEditorSupportsEveryStrategySourceStateTransition" in tests
    assert "CardRechargeEditorSerializesOneServerChoicePerCard" in tests
    assert "OrderedListActionsFollowTheServerConstraintContract" in tests
    assert "UltimateWeaponEditorPreservesUnknownValuesAndConstrainsStun" in tests
    assert "DormantIgnoreValueSurvivesInheritedAndReconstructedRows" in tests
    assert "PresetAndLocalDraftsSurviveFormAndSourceStateTransitions" in tests
    assert "PresetAndLocalDraftsSurviveSparseBaseTransitions" in tests
    assert "ModuleLocalEditorUsesEightFieldsAndPreventsRepeatedChoices" in tests
    assert (
        "ChangingOneModuleSelectionKeepsEverySelectionAvailableDuringPeerRefresh"
        in tests
    )
    assert (
        "ModulePresetPreviewUsesAuthoritativeEightSlotMetadataAndLifecycle"
        in tests
    )
    assert "ManagedModulePresetRequestsUsePresetOrCurrentLocalDefinition" in tests
    assert (
        "ModuleCatalogRefreshPreservesSelectionsAndExplicitlySelectsCreatedPreset"
        in tests
    )
    assert "MissingManagedCapabilityHidesCreationAndRetainsDraftOnFailure" in tests
    assert "VariantCreationIsAvailableFromAReadOnlySelectedPreset" in tests
    assert "ManagedModulePresetCreationResultRoundTripsWithoutPublication" in tests
    assert (
        "TargetPriorityLocalEditorRetainsCompleteMembershipInChangedOrder"
        in tests
    )
    assert "OrbLocalEditorEmitsExactlyThreeUnnormalizedTextFields" in tests
    assert "ComputedDisplayPropertiesRemainReadOnly" in tests
    assert "BasePinReviewCoversFirstAttachmentWithoutImplyingActivation" in tests


def test_wpf_rebase_and_publish_reviews_keep_activation_separate():
    xaml = _text("StrategyProfilesWindow.xaml")
    code = _text("StrategyProfilesWindow.xaml.cs")
    view_models = _text("StrategyAuthoringViewModels.cs")
    api_client = _text("ApiClient.cs")
    compatibility = _text("ControlSurfaceCompatibility.cs")

    assert 'x:Name="RebaseBanner"' in xaml
    assert 'Content="Review Base update..."' in xaml
    assert 'Content="Review &amp; Publish..."' in xaml
    assert 'operation = "preview_rebase"' in code
    assert "_reviewedRebaseFingerprint = response.ReviewedRebaseFingerprint" in code
    assert "InvalidateReviewedRebase();" in code
    assert "source.Base = _publishedBasePin is null" in code
    assert 'ReviewRebaseButton.Content = "Review Base selection..."' in code
    assert "RequiresReviewedBaseSelection()" in code
    assert "SameBaseReference(_publishedBasePin, SelectedBasePin())" in code
    assert "the draft pin was not changed" in code
    assert 'builder.AppendLine("BASE PIN REVIEW")' in view_models
    assert "draft's pinned Base reference" in view_models
    assert "StrategyAuthoringReviewFormatter.FormatPublishReview" in code
    assert "Publishing will not activate this Strategy" in view_models
    assert "Bases cannot be activated" in view_models
    assert '"/api/v1/strategy-authoring"' in api_client
    assert "MinimumServerRevision = 28" in compatibility
    assert '"managed_custom_module_presets_v1"' in compatibility
    assert '"strategy_authoring_local_loadout_editors_v1"' in compatibility
    assert '"strategy_revision_history_v1"' in compatibility
    assert '"strategy_authoring_profile_lifecycle_v1"' in compatibility
    assert '"strategy_authoring_specialized_editors_v1"' in compatibility
    assert '"strategy_authoring_v1"' in compatibility


def test_wpf_profile_local_loadout_controls_use_only_nested_server_metadata():
    xaml = _text("StrategyProfilesWindow.xaml")
    code = _text("StrategyProfilesWindow.xaml.cs")
    models = _text("Models.cs")
    view_models = _text("StrategyAuthoringViewModels.cs")
    structured = _text("StrategyStructuredEditorViewModels.cs")

    assert 'JsonPropertyName("local_editor")' in models
    assert 'JsonPropertyName("unique_field_values")' in models
    assert 'JsonPropertyName("profile_local_loadout_editors")' in models
    assert 'JsonPropertyName("preset_catalog")' in models
    assert "public int SchemaVersion { get; set; } = 3;" in models
    assert "_definition.Editor.LocalEditor is not null" in view_models
    assert "presetField.Key" in view_models
    assert "localMetadata.Key" in view_models
    assert "SelectedDefinitionForm.Key" in view_models
    assert "LocalDefinitionEditor?.CurrentValue" in view_models
    assert "SelectedPreset?.Value.Clone()" in view_models
    assert "dormantValue?.PresetValue" in view_models
    assert "dormantValue?.LocalValue" in view_models
    assert "UniqueFieldValues" in structured
    assert "RefreshUniqueFieldOptions" in structured
    assert "ServerNormalizedText" in structured
    assert 'AutomationProperties.Name="Definition source"' in xaml
    assert 'AutomationProperties.Name="Shared preset"' in xaml
    assert "MoveLocalDefinitionListItemUp_Click" in xaml
    assert "MoveLocalDefinitionListItemDown_Click" in xaml
    assert "row.LocalDefinitionEditor?.MoveListItem" in code
    assert "raw json" not in xaml.lower()


def test_wpf_module_preset_preview_and_save_as_new_flow_are_authoritative():
    xaml = _text("StrategyProfilesWindow.xaml")
    code = _text("StrategyProfilesWindow.xaml.cs")
    models = _text("Models.cs")
    view_models = _text("StrategyAuthoringViewModels.cs")
    dialog_xaml = _text("ModulePresetNameWindow.xaml")
    dialog_code = _text("ModulePresetNameWindow.xaml.cs")

    assert 'Text="MODULE PRESET DEFINITION"' in xaml
    assert (
        'ItemsSource="{Binding ModulePresetPreviewSlots, Mode=OneWay}"'
        in xaml
    )
    assert 'Text="{Binding Module, Mode=OneWay}"' in xaml
    assert 'Content="Create variant..."' in xaml
    assert 'Content="Save as preset..."' in xaml
    assert 'Click="CreateModuleVariant_Click"' in xaml
    assert 'Click="SaveModulePreset_Click"' in xaml
    assert "ModulePresetManagementVisible" in xaml

    for property_name in (
        "module_presets",
        "managed_custom_module_presets",
        "origin",
        "editable",
        "can_create_variant",
        "definition",
        "slots",
        "family",
        "role",
        "module",
    ):
        assert f'JsonPropertyName("{property_name}")' in models
    assert "Bundled preset • read-only" in models
    assert "Custom preset • immutable" in models
    assert 'Operation { get; set; } = "create_module_preset"' in models
    assert "JsonIgnoreCondition.WhenWritingNull" in models

    assert "SelectedModulePreset?.Slots" in view_models
    assert "BuildCreateModuleVariantRequest" in view_models
    assert "BuildSaveModulePresetRequest" in view_models
    assert "ReconcileModulePresetCatalog" in view_models
    assert "PresetOptions.Move" in view_models
    assert "PresetOptions.Clear" not in view_models
    assert "NotifyCollectionChangedAction.Reset" not in view_models
    assert "capabilities.ManagedCustomModulePresets" in view_models
    assert '"create_module_preset"' in view_models

    assert "response.Published" in code
    assert "response.PublicationActivatesStrategy" in code
    assert "response.Catalog.ModulePresets" in code
    assert "row.ReconcileModulePresetCatalog" in code
    assert "Validate → Review → Publish" in code
    assert "current draft and selections remain open" in code
    assert "No Base or Strategy was published" in code
    creation_flow = code.split(
        "private async Task CreateManagedModulePresetAsync", 1
    )[1].split("private void RefreshAuthoringActionButtons", 1)[0]
    assert "ApplyCatalog(response.Catalog)" not in creation_flow

    assert 'Text="Safe stable ID"' in dialog_xaml
    assert "IDs are immutable and must be new" in dialog_xaml
    assert "^[a-z][a-z0-9_]{2,47}$" in dialog_code


def test_wpf_custom_strategy_rename_and_delete_are_explicit_and_guarded():
    xaml = _text("StrategyProfilesWindow.xaml")
    code = _text("StrategyProfilesWindow.xaml.cs")
    models = _text("Models.cs")

    assert 'x:Name="RenameStrategyButton"' in xaml
    assert 'Content="Rename Strategy"' in xaml
    assert 'x:Name="DeleteStrategyButton"' in xaml
    assert 'Content="Delete Strategy..."' in xaml
    assert 'Style="{StaticResource DangerButton}"' in xaml
    assert "EntityIdBox.IsEnabled = editable && _isNew" in code
    assert "DisplayNameBox.Focus();" in code
    assert "The stable ID and activation remain unchanged" in code
    assert 'operation = "retire_strategy"' in code
    assert "expected_source_fingerprint = selected.SourceFingerprint" in code
    assert "MessageBoxResult.No" in code
    assert "currently selected, the server will refuse" in code
    assert "BuiltIn: false" in code
    assert 'JsonPropertyName("retired")' in models
    assert 'JsonPropertyName("retirement")' in models
    assert 'JsonPropertyName("recoverable")' in models


def test_wpf_history_review_restore_conflict_and_retired_lineage_workflow():
    authoring_xaml = _text("StrategyProfilesWindow.xaml")
    authoring_code = _text("StrategyProfilesWindow.xaml.cs")
    history_xaml = _text("StrategyHistoryWindow.xaml")
    history_code = _text("StrategyHistoryWindow.xaml.cs")
    api_client = _text("ApiClient.cs")
    models = _text("Models.cs")
    view_models = _text("StrategyAuthoringViewModels.cs")

    assert 'x:Name="HistoryButton"' in authoring_xaml
    assert 'Content="History..."' in authoring_xaml
    assert "new StrategyHistoryWindow(_api)" in authoring_code
    assert "history.StrategyRestored +=" in authoring_code
    assert "await LoadCatalogAsync(selectedKind, selectedId)" in authoring_code
    assert "Runtime selection and activation are unchanged" in authoring_code

    assert 'Text="Immutable Strategy History"' in history_xaml
    assert 'x:Name="LineagesList"' in history_xaml
    assert 'x:Name="RevisionsList"' in history_xaml
    assert 'Content="Compare with latest"' in history_xaml
    assert 'Content="Restore as new revision..."' in history_xaml
    assert "Retired lineage" in models
    assert 'operation = "publish_restore_strategy"' in history_code
    assert '"preview_restore_strategy"' in history_code
    assert '"compare_strategy_revision"' in history_code
    assert "expected_revision_fingerprint = _revision.RevisionFingerprint" in history_code
    assert "expected_latest_source_fingerprint = _lineage.LatestSourceFingerprint" in history_code
    assert "reviewed_restore_fingerprint = _review.ReviewedRestoreFingerprint" in history_code
    assert "RestoreButton.IsEnabled = false" in history_code
    assert "Any open Strategy draft remains unchanged" in history_code
    assert "will not select or activate the Strategy" in history_code
    assert "StrategyHistoryReviewFormatter.FormatComparison" in history_code
    assert "Generated plan fingerprint changed" in view_models
    assert "explicit Ignore changes" in view_models
    assert "Base pin or embedded snapshot changed" in view_models
    assert "Path" not in history_code
    assert "Delete" not in history_xaml

    assert '"/api/v1/strategy-authoring/history"' in api_client
    assert "GetStrategyRevisionAsync" in api_client
    assert 'JsonPropertyName("revision_fingerprint")' in models
    assert 'JsonPropertyName("publication_origin")' in models
    assert 'JsonPropertyName("current_validation_valid")' in models
    assert 'JsonPropertyName("comparison")' in models
    assert 'JsonPropertyName("generated_plan_changes")' in models
    assert 'JsonPropertyName("expanded_plan_exposed")' in models
