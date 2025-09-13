
core/run_state.py
core.run_state.AutomationControl — Class: thread-safe holder for run state and execution mode
core.run_state.AutomationControl.state (property) — R: RunState enum; S: [state] when set (validated & locked); E: ValueError on invalid string; TypeError on wrong type
core.run_state.AutomationControl.mode (property) — R: ExecMode enum; S: [state] when set (validated & locked); E: ValueError on invalid string; TypeError on wrong type
core.run_state.RunState — Class: Enum of run states {"RUNNING","PAUSED","STOPPED","UNKNOWN"}
core.run_state.ExecMode — Class: Enum of execution modes {"RETRY","WAIT","HOME"}
