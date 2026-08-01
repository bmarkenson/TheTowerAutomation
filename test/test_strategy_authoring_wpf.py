from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WPF = ROOT / "windows" / "TheTower.ControlSurface"


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
    assert "PropertyGroupDescription(nameof(AuthoringSettingRowViewModel.Section))" in code
    assert 'x:Name="SettingsList"' in xaml
    assert 'Text="{Binding CapabilityDisplay}"' in xaml
    assert 'Text="{Binding ProvenanceDisplay}"' in xaml
    assert 'Text="{Binding EffectivePolicyDisplay}"' in xaml
    assert 'Text="{Binding EffectiveValueDisplay}"' in xaml
    assert 'JsonPropertyName("observation_supported")' in models
    assert 'JsonPropertyName("repair_supported")' in models
    assert 'public StrategyAuthoringResolution Resolution { get; set; }' in models
    assert "resolution: item.Resolution" in code
    assert "Bases are never activatable" in xaml
    assert "Activate" not in xaml


def test_wpf_rows_support_source_states_filtering_and_lossless_fallback():
    xaml = _text("StrategyProfilesWindow.xaml")
    code = _text("StrategyProfilesWindow.xaml.cs")
    view_models = _text("StrategyAuthoringViewModels.cs")

    assert 'DisplayMemberPath="DisplayName"' in xaml
    assert 'SelectedItem="{Binding SelectedSourceState, Mode=TwoWay}"' in xaml
    assert 'Content="Reset to inherited"' in xaml
    assert 'Content="Show active only"' in xaml
    assert 'Content="Show all settings"' in xaml
    assert "item is AuthoringSettingRowViewModel { IsActive: true }" in code
    assert "capabilities.BaseSourceStates" in view_models
    assert "capabilities.StrategySourceStates" in view_models
    assert '"base" =>' in view_models
    assert '"local" => "Local Strategy override"' in view_models
    assert '"local_ignore" => "Explicit local Ignore"' in view_models
    assert 'EditorType is "perk_multiselect" or "perk_order"' in view_models
    assert 'EditorType == "preset"' in view_models
    assert 'EditorType is "fixed_value" or "damage_percentage"' in view_models
    assert "The value remains visible and round-trips unchanged" in view_models
    assert "return _retainedValue?.Clone();" in view_models
    assert "preserved unknown value" in view_models


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
    assert "MinimumServerRevision = 19" in compatibility
    assert '"strategy_authoring_v1"' in compatibility
