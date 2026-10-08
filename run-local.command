#!/usr/bin/env bash
# Chạy app trên máy (macOS/Linux). macOS: bấm đúp file này. Linux: ./run-local.command
# Sửa code trong app/ thì server tự tải lại, chỉ cần F5 trình duyệt.
set -e
cd "$(dirname "$0")"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then echo "Chưa có Python. Cài Python 3.11+ từ https://www.python.org/downloads/ rồi chạy lại."; read -r _; exit 1; fi
[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
echo "Đang cài thư viện (lần đầu mất 1-2 phút)..."
pip install -q -r requirements.txt
if [ ! -f .env.local ]; then
  python - <<'PYEOF'
import secrets
open(".env.local", "w", encoding="utf-8").write(
    "# Cấu hình chạy trên máy. Không commit file này.\n"
    f"SECRET_KEY={secrets.token_hex(32)}\n"
    "ADMIN_USERNAME=admin\nADMIN_PASSWORD=matkhau123\n"
    "COOKIE_SECURE=0\nDATA_DIR=./data\n"
    "# Dán key Claude vào đây nếu muốn AI trả lời (để trống thì chỉ trích tài liệu)\n"
    "ANTHROPIC_API_KEY=\n")
PYEOF
fi
set -a; . ./.env.local; set +a
PORT="${PORT:-8000}"
echo ""
echo "  Người dùng: http://localhost:$PORT/user/"
echo "  Quản trị:   http://localhost:$PORT/admin/   (admin / matkhau123)"
echo "  Dừng: bấm Ctrl+C"
echo ""
( sleep 3; (command -v open >/dev/null && open "http://localhost:$PORT/user/") || (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT/user/") || true ) >/dev/null 2>&1 &
exec uvicorn app.main:create_app --factory --reload --reload-dir app --port "$PORT"
