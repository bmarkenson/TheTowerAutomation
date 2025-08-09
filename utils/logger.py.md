$PROJECT_ROOT/utils/logger.py — Library
utils.logger.log(msg, level="INFO") — Returns: None (writes formatted log entry to stdout and logs/actions.log); Side effects: [fs][log]; Defaults: log level defaults to "INFO"; Ensures logs/ directory exists before writing; Errors: OSError if unable to create directory or write file.
