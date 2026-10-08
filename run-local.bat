@echo off
rem Chạy app trên máy Windows: bấm đúp file này.
rem Sửa code trong app\ thì server tự tải lại, chỉ cần F5 trình duyệt.
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Chua co Python. Cai Python 3.11+ tu https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) roi chay lai. & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
echo Dang cai thu vien (lan dau mat 1-2 phut)...
pip install -q -r requirements.txt || (pause & exit /b 1)
if not exist .env.local python -c "import secrets;open('.env.local','w',encoding='utf-8').write('# Cau hinh chay tren may. Khong commit file nay.\nSECRET_KEY='+secrets.token_hex(32)+'\nADMIN_USERNAME=admin\nADMIN_PASSWORD=matkhau123\nCOOKIE_SECURE=0\nDATA_DIR=./data\n# Dan key Claude vao day neu muon AI tra loi\nANTHROPIC_API_KEY=\n')"
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env.local") do set "%%a=%%b"
echo.
echo   Nguoi dung: http://localhost:8000/user/
echo   Quan tri:   http://localhost:8000/admin/   (admin / matkhau123)
echo   Dung: bam Ctrl+C
echo.
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8000/user/"
uvicorn app.main:create_app --factory --reload --reload-dir app --port 8000
pause
