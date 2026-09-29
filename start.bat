@echo off
cd /d "%~dp0"
title FoodOrder

rem Asks for the Groq key only if it is not already saved on this PC.
if not defined GROQ_API_KEY set /p "GROQ_API_KEY=Paste your Groq API key: "

rem ngrok opens in its own window. Keep it open while you use the site.
start "ngrok" cmd /k ngrok http 3000 --url https://negotiate-sauciness-punk.ngrok-free.dev

echo Starting the site on port 3000...
python app.py
pause