$PROJECT_ROOT/core/automation_state.py — Library
core.automation_state.AutomationControl — Class: thread-safe holder for run state and execution mode
core.automation_state.AutomationControl.state (property) — Returns: RunState enum; Side effects: [state] when set (validated & locked); Errors: ValueError on invalid string; TypeError on wrong type
core.automation_state.AutomationControl.mode (property) — Returns: ExecMode enum; Side effects: [state] when set (validated & locked); Errors: ValueError on invalid string; TypeError on wrong type
core.automation_state.RunState — Class: Enum of run states {"RUNNING","PAUSED","STOPPED","UNKNOWN"}
core.automation_state.ExecMode — Class: Enum of execution modes {"RETRY","WAIT","HOME"}
