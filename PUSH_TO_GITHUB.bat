@echo off
chcp 65001 >nul
title Yana - zalivka na GitHub
cd /d "%~dp0"
echo ========================================================
echo  1) Sozdaj PUSTOY repozitoriy na https://github.com/new
echo     (bez README, bez .gitignore - prosto nazvanie Yana)
echo  2) Vstav syuda ego ssylku
echo ========================================================
echo.
set /p URL="Ssylka (https://github.com/USER/Yana.git): "
if "%URL%"=="" (
  echo Ssylka pustaya. Zapusti esche raz.
  pause
  exit /b 1
)
git init 2>nul >nul
git add -A
git commit -m "Yana 8.0" 2>nul >nul
git branch -M main
git remote remove origin 2>nul >nul
git remote add origin %URL%
git push -u origin main --force
if errorlevel 1 (
  echo.
  echo [!!!] Ne poluchilos. Prover ssylku i chto git ustanovlen.
  pause
  exit /b 1
)
echo.
echo ========================================================
echo  GOTOVO! Na GitHub vo vkladke Actions uzhe idet sborka.
echo  Cherez ~10-15 minut zaberi Yana.exe iz vkladki Releases.
echo ========================================================
pause
