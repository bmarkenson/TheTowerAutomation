utils/previous_wave.py
utils.previous_wave.get_previous_run_wave(records_dir="logs/battles") — R: previous run’s final wave as int|None from the newest structured Battle JSON record; S: [fs]; E: returns None if no record exists or the latest record cannot provide a wave.
utils.previous_wave.main() — R: action result (CLI output only); S: [fs]; CLI flags: --records-dir; E: same as get_previous_run_wave; exits after printing result.
