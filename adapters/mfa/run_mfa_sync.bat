@echo off
rem mfa adapter: run nightly from Task Scheduler, or by hand after a same-day change.
rem Appends to logs\mfa_sync.log. A non-zero exit code means failure (see README).
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
python mfa_sync_adapter.py --config mfa_adapter.ini --log-file logs\mfa_sync.log %*
exit /b %ERRORLEVEL%
