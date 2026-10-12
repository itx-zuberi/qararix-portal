@echo off
cd /d "%~dp0"
if not exist venv ( python -m venv venv )
call venv\Scripts\activate
pip install -q -r requirements.txt
echo.
echo Starting Qararix Portal on http://localhost:5000
python app.py
pause
