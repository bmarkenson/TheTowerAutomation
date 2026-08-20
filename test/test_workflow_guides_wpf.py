from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "windows" / "TheTower.ControlSurface"


def _text(name: str) -> str:
    return (CLIENT / name).read_text(encoding="utf-8")


def test_workflow_guides_are_reachable_from_help_and_contexts():
    main_xaml = _text("MainWindow.xaml")
    main_code = _text("MainWindow.xaml.cs")
    strategy_xaml = _text("StrategyProfilesWindow.xaml")
    strategy_code = _text("StrategyProfilesWindow.xaml.cs")

    assert 'Header="_Help"' in main_xaml
    assert 'Header="Workflow guides…"' in main_xaml
    assert 'Content="Move-PC guide…"' in main_xaml
    assert 'Content="Restart guide…"' in main_xaml
    assert 'Content="Editing guide…"' in strategy_xaml

    assert "OpenWorkflowGuide(WorkflowGuideIds.Controls)" in main_code
    assert "OpenWorkflowGuide(WorkflowGuideIds.MoveEmulator)" in main_code
    assert "OpenWorkflowGuide(WorkflowGuideIds.RestartBlueStacks)" in main_code
    assert "WorkflowGuideIds.EditStrategy" in strategy_code
    assert "if (!StrategyProfilesMenuItem.IsEnabled)" in main_code


def test_workflow_guide_window_is_read_only_and_navigates_to_guarded_owners():
    xaml = _text("WorkflowGuidesWindow.xaml")
    code = _text("WorkflowGuidesWindow.xaml.cs")

    assert "Version-matched, read-only guidance" in xaml
    assert "they never perform an action" in xaml
    assert "GuideList_SelectionChanged" in xaml
    assert "Navigate_Click" in xaml
    assert "Action<WorkflowGuideDestination>" in code
    assert "ControlSurfaceApi" not in code
    assert "PostControl" not in code
    assert "BlueStacksInstanceController" not in code


def test_workflow_guide_catalog_routes_to_canonical_detailed_owners():
    catalog = _text("WorkflowGuideCatalog.cs")

    assert "Managed Runtime Operations → Move the emulator between Windows PCs" in catalog
    assert "Control Surface Architecture → Automatic BlueStacks degradation recovery" in catalog
    assert "Strategy Authoring Architecture → GUI contract" in catalog
    assert "Control Surface Architecture → Current GUI capabilities" in catalog
