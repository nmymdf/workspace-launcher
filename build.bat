@echo off
REM 本機打包（有 Python 3.12 時使用）；平常用 GitHub Actions 雲端編譯即可
pip install pyinstaller tkinterdnd2
for /f %%v in ('python -c "import re;print(re.search(r'APP_VERSION = \"(.+?)\"', open('workspace_launcher.py', encoding='utf-8').read()).group(1))"') do set VER=%%v
pyinstaller --onefile --noconsole --collect-all tkinterdnd2 --icon app.ico --add-data "app.ico;." --name WorkspaceLauncher_%VER% workspace_launcher.py
echo 完成：dist\WorkspaceLauncher_%VER%.exe
pause
