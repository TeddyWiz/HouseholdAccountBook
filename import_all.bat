@echo off
chcp 65001 > nul
cd /d %~dp0
set PYTHONIOENCODING=utf-8
rem 사용법: import_all.bat [PDF암호] [폴더]
python import_all.py %*
pause
