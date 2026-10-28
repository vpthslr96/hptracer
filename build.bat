@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Устанавливаю зависимости...
python -m pip install -r requirements.txt pyinstaller || goto :error
echo Собираю HPWatcher.exe...
python -m PyInstaller --noconfirm --onefile --uac-admin --hidden-import settings_ui --name HPWatcher hp_watcher.py || goto :error
if exist config.json copy /Y config.json dist\ >nul
echo.
echo Готово: dist\HPWatcher.exe  (config.json кладите рядом с ним)
pause
exit /b 0
:error
echo.
echo Сборка не удалась, смотрите сообщения выше.
pause
exit /b 1
