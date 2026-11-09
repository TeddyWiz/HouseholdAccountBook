@echo off
chcp 65001 > nul
cd /d %~dp0
python -m pip install -r requirements.txt
echo.
echo 설치 완료. import_all.bat 로 DataSource 폴더 전체를 넣고, run_app.bat 또는 run_web.bat 로 실행하세요.
pause
