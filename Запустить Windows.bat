@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.12 -m venv .venv
    if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
if errorlevel 1 goto failed
if not exist .env copy .env.example .env >nul
.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless false --browser.gatherUsageStats false
if errorlevel 1 goto failed
exit /b 0
:failed
echo Could not start. Install Python 3.12 from python.org and try again.
echo If Python is installed, copy the error text above, without any keys.
pause
exit /b 1
