@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if not exist ".streamlit\secrets.toml" (
  echo.
  echo UPSTOX_ACCESS_TOKEN is not configured.
  echo Copy secrets.toml.example to .streamlit\secrets.toml and paste your existing token there.
  echo.
  pause
  exit /b 1
)
python -m streamlit run app.py
pause
