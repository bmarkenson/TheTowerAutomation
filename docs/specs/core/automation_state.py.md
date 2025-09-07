
core/automation_state.py
core.automation_state.AutomationControl — Class: thread-safe holder for run state and execution mode
core.automation_state.AutomationControl.state (property) — R: RunState enum; S: [state] when set (validated & locked); E: ValueError on invalid string; TypeError on wrong type
core.automation_state.AutomationControl.mode (property) — R: ExecMode enum; S: [state] when set (validated & locked); E: ValueError on invalid string; TypeError on wrong type
core.automation_state.RunState — Class: Enum of run states {"RUNNING","PAUSED","STOPPED","UNKNOWN"}
core.automation_state.ExecMode — Class: Enum of execution modes {"RETRY","WAIT","HOME"}
