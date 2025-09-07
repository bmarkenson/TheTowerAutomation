
utils/logger.py
utils.logger.log(msg, level="INFO") — R: None (writes formatted log entry to stdout and logs/actions.log); S: [fs][log]; Defaults: log level defaults to "INFO"; Ensures logs/ directory exists before writing; E: OSError if unable to create directory or write file.
