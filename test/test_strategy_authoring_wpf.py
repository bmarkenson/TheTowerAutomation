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
    assert "BasePinBox.IsEnabled = editable && _isNew" in code
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
    assert "ComputedDisplayPropertiesRemainReadOnly" in tests


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
    assert "source.Base = CloneBaseReference(_rebaseOriginalBase);" in code
    assert "the pinned revision was not changed" in code
    assert "StrategyAuthoringReviewFormatter.FormatPublishReview" in code
    assert "Publishing will not activate this Strategy" in view_models
    assert "Bases cannot be activated" in view_models
    assert '"/api/v1/strategy-authoring"' in api_client
    assert "MinimumServerRevision = 20" in compatibility
    assert '"strategy_authoring_specialized_editors_v1"' in compatibility
    assert '"strategy_authoring_v1"' in compatibility
