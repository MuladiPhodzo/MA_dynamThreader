@echo off
setlocal
cd /d "%~dp0"

rem Start Angular dev server for the dashboard UI
npm run start
