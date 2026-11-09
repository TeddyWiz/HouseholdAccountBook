@echo off
chcp 65001 > nul
cd /d %~dp0
set PYTHONIOENCODING=utf-8
echo 웹페이지: http://localhost:5000   (종료: 이 창을 닫으세요)
echo 휴대폰 등 다른 기기에서 보려면 run_web.bat 대신:  python web_app.py --lan
python web_app.py
