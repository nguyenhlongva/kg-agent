# KB Agent: trợ lý tri thức có trang người dùng và trang quản trị

- Người dùng: `https://khanhhuyen.ai.zalopay.xyz/user/` (đường dẫn gốc `/` tự chuyển về `/user/`). Đăng nhập bằng tên Zalo và số điện thoại, giao diện chat có lịch sử giống Claude.
- Quản trị: `https://khanhhuyen.ai.zalopay.xyz/admin/`. Đăng nhập bằng tài khoản quản trị. Có hộp thư xem mọi cuộc trò chuyện, bật/tắt AI (toàn cục hoặc từng cuộc), trả lời tay, gợi ý bằng AI, sửa/xóa tin nhắn, quản lý người dùng, nạp kiến thức, quản lý tài khoản quản trị và cài đặt.

## Kiến trúc

```
Trình duyệt ──HTTPS──> nginx ──> container kb-agent (FastAPI, cổng 8000)
                                   ├─ SQLite: /data/kb.sqlite3 (người dùng, chat, tài liệu, cài đặt)
                                   └─ Claude API (api.anthropic.com) qua ANTHROPIC_API_KEY
```

Luồng trả lời: tìm đoạn tài liệu khớp nhất (BM25 tiếng Việt, có thể bật thêm embedding). Dưới ngưỡng thì chuyển nhân viên, không gọi AI. Đạt ngưỡng thì Claude trả lời chỉ từ các đoạn đó và phải kèm mã trích dẫn. Trích dẫn không hợp lệ bị xóa, không còn trích dẫn nào thì chuyển nhân viên. Nếu cuộc trò chuyện đang tắt AI, tin nhắn chờ nhân viên trả lời tay.

## Triển khai (dành cho team Dev)

Yêu cầu: server Linux có Docker và Docker Compose, nginx, và server gọi ra được `api.anthropic.com:443`.

1. **DNS**: tạo bản ghi A `khanhhuyen.ai.zalopay.xyz` trỏ về IP server.

2. **Mã nguồn và cấu hình**
   ```bash
   sudo mkdir -p /opt/kb-agent && cd /opt/kb-agent
   # chép toàn bộ thư mục này vào /opt/kb-agent
   cp .env.example .env
   python3 -c "import secrets;print(secrets.token_hex(32))"   # dán vào SECRET_KEY
   nano .env   # điền SECRET_KEY, ADMIN_PASSWORD, ANTHROPIC_API_KEY
   chmod 600 .env
   mkdir -p data && sudo chown 10001 data
   ```

3. **Chạy ứng dụng**
   ```bash
   docker compose up -d --build
   curl http://127.0.0.1:8000/health     # {"ok":true,...,"llm":true}
   ```

4. **HTTPS và nginx**
   ```bash
   sudo cp deploy/nginx.conf /etc/nginx/sites-available/khanhhuyen.ai.zalopay.xyz
   sudo ln -s /etc/nginx/sites-available/khanhhuyen.ai.zalopay.xyz /etc/nginx/sites-enabled/
   # Lấy chứng chỉ. Nếu công ty đã có chứng chỉ wildcard *.ai.zalopay.xyz thì sửa đường dẫn ssl_certificate cho đúng
   sudo certbot certonly --webroot -w /var/www/certbot -d khanhhuyen.ai.zalopay.xyz
   sudo nginx -t && sudo systemctl reload nginx
   ```
   Nếu dùng load balancer hoặc ingress khác thay nginx: tắt buffer cho đường dẫn `/api/chat` để câu trả lời hiện dần, timeout đọc tối thiểu 180 giây, cho phép upload tối thiểu 30 MB, và chuyển tiếp header `X-Forwarded-For`, `X-Forwarded-Proto`.

5. **Sao lưu**: thêm vào crontab `0 2 * * * /opt/kb-agent/deploy/backup.sh` (cần gói `sqlite3`). Toàn bộ dữ liệu nằm trong `data/`.

6. **Đăng nhập lần đầu**: vào `/admin/` bằng `ADMIN_USERNAME` / `ADMIN_PASSWORD`, đổi mật khẩu ngay ở mục "Đổi mật khẩu", rồi tạo thêm tài khoản quản trị cho đồng nghiệp. Sau lần chạy đầu có thể xóa `ADMIN_PASSWORD` khỏi `.env`.

Cập nhật phiên bản mới: chép mã mới đè lên rồi chạy `docker compose up -d --build`. Dữ liệu trong `data/` được giữ nguyên.

## Biến môi trường

| Biến | Ý nghĩa |
|---|---|
| `SECRET_KEY` | Bắt buộc, tối thiểu 32 ký tự, dùng ký phiên đăng nhập. Đổi giá trị này sẽ đăng xuất mọi người |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Tài khoản quản trị đầu tiên, chỉ dùng khi database chưa có quản trị viên |
| `ANTHROPIC_API_KEY` | Key Claude API. Để trống thì hệ thống chỉ trích nguyên văn đoạn tài liệu liên quan nhất |
| `LLM_MODEL`, `LLM_FAST_MODEL` | Model trả lời và model mở rộng từ khóa. Kiểm tra tên model hiện hành tại docs.claude.com |
| `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_MODEL` | Tùy chọn, bật tìm kiếm ngữ nghĩa (API kiểu OpenAI `/embeddings`). Đổi model embedding thì phải nạp lại tài liệu |
| `COOKIE_SECURE` | Giữ `1` khi chạy HTTPS. Chỉ đặt `0` khi thử ở localhost |
| `USER_SESSION_DAYS`, `ADMIN_SESSION_HOURS` | Thời hạn phiên đăng nhập |
| `MAX_UPLOAD_MB` | Dung lượng tối đa mỗi file tài liệu |

## Chạy thử trên máy

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))") ADMIN_PASSWORD=matkhau123 COOKIE_SECURE=0 DATA_DIR=./data
export ANTHROPIC_API_KEY=sk-ant-...      # có thể bỏ qua khi chỉ thử giao diện
uvicorn app.main:create_app --factory --port 8000
pytest                                    # kiểm thử, không cần mạng hay key
```

## Bảo mật và giới hạn cần biết

- **Đăng nhập người dùng không có mật khẩu.** Ai biết số điện thoại của người khác có thể đăng nhập thay và xem lịch sử chat của họ. Phù hợp cho thử nghiệm nội bộ. Trước khi mở rộng, nên thêm OTP qua SMS/ZNS hoặc Zalo Login.
- Mật khẩu quản trị băm PBKDF2. Phiên đăng nhập là cookie HttpOnly, Secure, SameSite=Lax. Đổi mật khẩu sẽ vô hiệu phiên cũ. Có giới hạn 10 lần đăng nhập mỗi 5 phút mỗi IP và 20 tin nhắn mỗi phút mỗi người dùng.
- Trang `/admin/` nên được giới hạn thêm theo IP hoặc VPN công ty ở tầng nginx nếu có thể.
- Câu hỏi và các đoạn tài liệu liên quan được gửi tới Claude API. Kiểm tra chính sách dữ liệu của công ty trước khi nạp tài liệu nhạy cảm.
- SQLite chạy tốt với vài chục đến vài trăm người dùng đồng thời trên một máy. Khi cần nhiều máy chủ, chuyển sang PostgreSQL.
- Người dùng nhận trả lời tay bằng cách trang tự hỏi lại server mỗi 4 giây khi đang mở. Không có thông báo đẩy về Zalo.
