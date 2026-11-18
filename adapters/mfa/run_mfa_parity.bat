@echo off
rem mfa parity check: run nightly from Task Scheduler right after run_mfa_sync.bat (e.g. 02:15).
rem Copies the legacy mfa.txt next to the adapter's newest archive copy and compares the two.
rem Appends the summary to logs\mfa_parity.log. Exit 0 = OK, 1 = NG, 2 = input error (see README).
rem Roll-up for the sign-off: run_mfa_parity.bat --rollup --from 2026-11-17 --to 2026-11-30
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
if "%~1"=="" (
  python parity_report.py --nightly --config mfa_adapter.ini --log-file logs\mfa_parity.log
) else (
  python parity_report.py --config mfa_adapter.ini --log-file logs\mfa_parity.log %*
)
exit /b %ERRORLEVEL%
