@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "ROOT=%~dp0"
set "UPDATER=%ROOT%tools\install_authenticated_downloads_v032.py"
set "DEFAULT_URL=http://127.0.0.1:8080"

if not exist "%UPDATER%" (
  echo ERROR: Missing updater: "%UPDATER%"
  exit /b 2
)

set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  echo ERROR: Python 3 was not found. Use the Python bundled with your existing AutoGenBook installation or install Python 3.10+.
  exit /b 2
)

if not "%~1"=="" goto run_with_arguments

set "OPENWEBUI_URL=%DEFAULT_URL%"
set /p "OPENWEBUI_URL=Open WebUI URL [%DEFAULT_URL%]: "
if not defined OPENWEBUI_URL set "OPENWEBUI_URL=%DEFAULT_URL%"

set "KEY_FILE="
set /p "KEY_FILE=Path to TXT file with an Open WebUI ADMIN API key: "
if not defined KEY_FILE (
  echo ERROR: The API-key TXT path is required for automatic Function update.
  exit /b 2
)
set "KEY_FILE=%KEY_FILE:"=%"

%PYTHON_CMD% "%UPDATER%" --openwebui-url "%OPENWEBUI_URL%" --webui-api-key-file "%KEY_FILE%" --output-dir "%ROOT%dist"
set "RC=%ERRORLEVEL%"
goto finish

:run_with_arguments
%PYTHON_CMD% "%UPDATER%" --output-dir "%ROOT%dist" %*
set "RC=%ERRORLEVEL%"

:finish
if not "%RC%"=="0" (
  echo.
  echo AutoGenBook update FAILED with exit code %RC%.
  echo Run this command from an ordinary Command Prompt and check the message above.
  exit /b %RC%
)

echo.
echo AutoGenBook Open WebUI Function 0.3.2 was prepared and updated.
echo Restart Open WebUI Desktop. Then send: vystupy JOB_ID
exit /b 0
