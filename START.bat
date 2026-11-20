@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo   NIFTY 500 RSI + EMA SIGNAL DESK
echo ================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo Python nahi mila. Python 3.10 ya newer install karo aur PATH enable karo.
  pause
  exit /b 1
)
python -m pip install --upgrade pip
if errorlevel 1 goto failed
python -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Scanner browser mein open hoga. Band karne ke liye is window mein Ctrl+C dabao.
python -m streamlit run app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Setup ya launch mein error aaya. Upar diya error screenshot bhejo.
pause
