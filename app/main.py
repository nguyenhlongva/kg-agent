"""API FastAPI: trang người dùng (/), trang quản trị (/admin/), hỏi đáp RAG, trả lời tay."""
import json
import os
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, rag
from .config import settings

STATIC = os.path.join(os.path.dirname(__file__), "static")
FALLBACK_CODES_NOTE = "Hệ thống AI đang tắt, đây là đoạn tài liệu liên quan nhất:"


# ---------- Mô hình dữ liệu vào ----------
class UserLogin(BaseModel):
    zalo_name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=1, max_length=20)


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class ChatIn(BaseModel):
    conversation_id: Optional[int] = None
    text: str = Field(min_length=1, max_length=4000)


class AdminIn(BaseModel):
    username: str = Field(min_length=3, max_length=60, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(default="", max_length=80)
    password: str = Field(min_length=8, max_length=200)


class AdminPatch(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=80)
    password: Optional[str] = Field(default=None, min_length=8, max_length=200)
    active: Optional[bool] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class UserIn(BaseModel):
    zalo_name: str = Field(min_length=1, max_length=80)
    phone: str
    note: str = Field(default="", max_length=500)


class UserPatch(BaseModel):
    zalo_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    phone: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=500)
    blocked: Optional[bool] = None


class ConvPatch(BaseModel):
    ai_mode: Optional[str] = Field(default=None, pattern=r"^(auto|on|off)$")
    needs_human: Optional[bool] = None
    title: Optional[str] = Field(default=None, max_length=120)


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


# ---------- Tiện ích ----------
def set_cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")


def ai_on(conv: dict, cfg: dict) -> bool:
    m = conv.get("ai_mode") or "auto"
    return m == "on" or (m == "auto" and bool(cfg.get("ai_enabled", True)))


def msg_out(m: dict) -> dict:
    return {"id": m["id"], "role": m["role"], "text": m["text"], "sources": source_details(json.loads(m["sources"] or "[]")),
            "escalated": bool(m["escalated"]), "fallback": bool(m["fallback"]), "created_at": m["created_at"],
            "edited_at": m.get("edited_at"), "admin_name": m.get("admin_name")}


def source_details(ids: list[str]) -> list[dict]:
    out = []
    for cid in ids:
        c = rag.index.by_id.get(cid)
        if c:
            out.append({"id": cid, "title": c["title"], "text": c["text"]})
    return out


def conv_out(c: dict, cfg: dict) -> dict:
    return {"id": c["id"], "title": c["title"], "ai_mode": c["ai_mode"], "ai_active": ai_on(c, cfg),
            "needs_human": bool(c["needs_human"]), "hidden_by_user": bool(c["hidden_by_user"]),
            "last_text": c["last_text"], "last_role": c["last_role"], "created_at": c["created_at"],
            "updated_at": c["updated_at"], "user_id": c["user_id"],
            "zalo_name": c.get("zalo_name"), "phone": c.get("phone")}


def add_message(conv_id: int, role: str, text: str, sources: list[str] | None = None, escalated=False,
                fallback=False, admin_id: int | None = None, needs_human: Optional[bool] = None,
                ai_mode: Optional[str] = None) -> dict:
    t = db.now()
    mid = db.ex("INSERT INTO messages(conversation_id, role, text, sources, escalated, fallback, admin_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (conv_id, role, text, json.dumps(sources or []), int(escalated), int(fallback), admin_id, t))
    sets, params = ["updated_at=?", "last_text=?", "last_role=?"], [t, text[:200], role]
    if needs_human is not None:
        sets.append("needs_human=?"); params.append(int(needs_human))
    if ai_mode is not None:
        sets.append("ai_mode=?"); params.append(ai_mode)
    db.ex(f"UPDATE conversations SET {', '.join(sets)} WHERE id=?", (*params, conv_id))
    return db.q1("SELECT * FROM messages WHERE id=?", (mid,))


def ensure_admin_seed() -> None:
    if db.q1("SELECT id FROM admins LIMIT 1"):
        return
    if not settings.admin_password or len(settings.admin_password) < 8:
        raise RuntimeError("Chưa có tài khoản quản trị. Đặt ADMIN_USERNAME và ADMIN_PASSWORD (ít nhất 8 ký tự) cho lần chạy đầu.")
    db.ex("INSERT INTO admins(username, display_name, password_hash, created_at) VALUES(?,?,?,?)",
          (settings.admin_username, "Quản trị viên", auth.hash_password(settings.admin_password), db.now()))


def ingest_document(filename: str, data: bytes, doc_id: str, title: str, scope: str, version: int) -> dict:
    text = rag.extract_text(filename, data).strip()
    if len(text) < 20:
        raise ValueError("File gần như không có chữ (có thể là PDF scan, cần OCR trước)")
    pieces = rag.chunk_text(text)
    ids = [f"{doc_id}-v{version}-c{i:03d}" for i in range(len(pieces))]
    vectors = rag.embed(pieces) if settings.embed_ready else None
    stmts: list[tuple[str, tuple]] = [
        ("DELETE FROM chunks WHERE doc_id=?", (doc_id,)),
        ("INSERT INTO documents(doc_id, title, scope, version, file_name, chunk_count, chars, updated_at) VALUES(?,?,?,?,?,?,?,?) "
         "ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title, scope=excluded.scope, version=excluded.version, "
         "file_name=excluded.file_name, chunk_count=excluded.chunk_count, chars=excluded.chars, updated_at=excluded.updated_at",
         (doc_id, title, scope, version, filename, len(pieces), len(text), db.now())),
    ]
    for i, (cid, p) in enumerate(zip(ids, pieces)):
        emb = json.dumps(vectors[i]) if vectors else None
        stmts.append(("INSERT INTO chunks(id, doc_id, idx, text, embedding) VALUES(?,?,?,?,?)", (cid, doc_id, i, p, emb)))
    db.ex_many(stmts)
    rag.index.rebuild()
    return {"doc_id": doc_id, "title": title, "chunks": len(pieces)}


# ---------- Ứng dụng ----------
def create_app() -> FastAPI:
    settings.validate()
    db.conn()
    ensure_admin_seed()
    rag.index.rebuild()

    app = FastAPI(title="KB Agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # ----- Trang -----
    @app.get("/", include_in_schema=False)
    def root_redirect():
        return RedirectResponse("/user/")

    @app.get("/user", include_in_schema=False)
    def user_redirect():
        return RedirectResponse("/user/")

    @app.get("/user/", include_in_schema=False)
    def user_page():
        return FileResponse(os.path.join(STATIC, "user.html"))

    @app.get("/admin", include_in_schema=False)
    def admin_redirect():
        return RedirectResponse("/admin/")

    @app.get("/admin/", include_in_schema=False)
    def admin_page():
        return FileResponse(os.path.join(STATIC, "admin.html"))

    @app.get("/health")
    def health():
        return {"ok": True, "chunks": len(rag.index.chunks), "llm": settings.llm_ready, "embedding": settings.embed_ready}

    @app.get("/api/public/settings")
    def public_settings():
        cfg = db.get_settings()
        return {k: cfg[k] for k in ("name", "suggest", "welcome", "wait")}

    # ----- Người dùng: đăng nhập bằng tên Zalo + số điện thoại -----
    @app.post("/api/auth/login")
    def user_login(body: UserLogin, request: Request, response: Response):
        auth.login_limiter.check("u:" + auth.client_ip(request))
        phone = auth.normalize_phone(body.phone)
        if not phone:
            raise HTTPException(400, "Số điện thoại không hợp lệ. Nhập 10 số, ví dụ 0901234567.")
        name = " ".join(body.zalo_name.split())
        u = db.q1("SELECT * FROM users WHERE phone=?", (phone,))
        if u and u["blocked"]:
            raise HTTPException(403, "Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên.")
        if u:
            db.ex("UPDATE users SET zalo_name=?, last_seen=? WHERE id=?", (name, db.now(), u["id"]))
            uid, is_new = u["id"], False
        else:
            uid, is_new = db.ex("INSERT INTO users(zalo_name, phone, created_at, last_seen) VALUES(?,?,?,?)",
                                (name, phone, db.now(), db.now())), True
        set_cookie(response, auth.USER_COOKIE, auth.make_token("user", uid, settings.user_session_days * 86400),
                   settings.user_session_days * 86400)
        return {"id": uid, "zalo_name": name, "phone": phone, "new": is_new}

    @app.post("/api/auth/logout")
    def user_logout(response: Response):
        response.delete_cookie(auth.USER_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(u=Depends(auth.current_user)):
        db.ex("UPDATE users SET last_seen=? WHERE id=?", (db.now(), u["id"]))
        return {"id": u["id"], "zalo_name": u["zalo_name"], "phone": u["phone"]}

    @app.get("/api/conversations")
    def my_conversations(u=Depends(auth.current_user)):
        cfg = db.get_settings()
        rows = db.q("SELECT * FROM conversations WHERE user_id=? AND hidden_by_user=0 ORDER BY updated_at DESC LIMIT 200", (u["id"],))
        return [conv_out(c, cfg) for c in rows]

    def own_conv(cid: int, u: dict) -> dict:
        c = db.q1("SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, u["id"]))
        if not c:
            raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
        return c

    @app.get("/api/conversations/{cid}/messages")
    def my_messages(cid: int, after: int = 0, u=Depends(auth.current_user)):
        c = own_conv(cid, u)
        rows = db.q("SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id LIMIT 500", (cid, after))
        return {"conversation": conv_out(c, db.get_settings()), "messages": [msg_out(m) for m in rows]}

    @app.post("/api/conversations/{cid}/hide")
    def hide_conv(cid: int, u=Depends(auth.current_user)):
        own_conv(cid, u)
        db.ex("UPDATE conversations SET hidden_by_user=1 WHERE id=?", (cid,))
        return {"ok": True}

    @app.post("/api/chat")
    def chat(body: ChatIn, request: Request, u=Depends(auth.current_user)):
        auth.chat_limiter.check("c:" + str(u["id"]))
        cfg = db.get_settings()
        text = body.text.strip()
        if body.conversation_id:
            conv = own_conv(body.conversation_id, u)
        else:
            t = db.now()
            cid = db.ex("INSERT INTO conversations(user_id, title, created_at, updated_at) VALUES(?,?,?,?)",
                        (u["id"], text[:80], t, t))
            conv = db.q1("SELECT * FROM conversations WHERE id=?", (cid,))
        history = db.q("SELECT role, text FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 6", (conv["id"],))[::-1]
        user_msg = add_message(conv["id"], "user", text)

        def gen():
            def line(obj):
                return json.dumps(obj, ensure_ascii=False) + "\n"
            yield line({"type": "conversation", "conversation": conv_out(conv, cfg)})
            yield line({"type": "user_message", "message": msg_out(user_msg)})
            fresh = db.q1("SELECT * FROM conversations WHERE id=?", (conv["id"],))
            if not ai_on(fresh, cfg):
                db.ex("UPDATE conversations SET needs_human=1 WHERE id=?", (conv["id"],))
                yield line({"type": "waiting", "text": cfg["wait"]})
                return
            yield line({"type": "status", "text": "Đang tìm trong tài liệu…"})
            r = rag.retrieve(text, history, cfg)
            if not r["ok"]:
                m = add_message(conv["id"], "ai", cfg["escalate"], escalated=True, needs_human=True)
                yield line({"type": "message", "message": msg_out(m)})
                return
            ctx = r["ctx"]
            valid = {c["id"] for c in ctx}
            if not settings.llm_ready:
                top = ctx[0]
                m = add_message(conv["id"], "ai", f"{FALLBACK_CODES_NOTE} [{top['id']}]", sources=[top["id"]], fallback=True)
                yield line({"type": "message", "message": msg_out(m)})
                return
            full = ""
            try:
                for piece in rag.stream(rag.SYSTEM_PROMPT, [{"role": "user", "content": rag.build_user_prompt(text, ctx, history)}]):
                    full += piece
                    if not full.lstrip().startswith("KHONG"):
                        yield line({"type": "delta", "text": piece})
            except rag.LLMError:
                m = add_message(conv["id"], "ai", cfg["ai_error"], escalated=True, needs_human=True)
                yield line({"type": "message", "message": msg_out(m)})
                return
            ids = rag.cited_ids(full, valid)
            if rag.SENTINEL in full or not ids:
                m = add_message(conv["id"], "ai", cfg["escalate"], escalated=True, needs_human=True)
            else:
                m = add_message(conv["id"], "ai", rag.clean_citations(full, valid).strip(), sources=ids)
            yield line({"type": "message", "message": msg_out(m)})

        return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})

    # ----- Quản trị: đăng nhập -----
    @app.post("/api/admin/login")
    def admin_login(body: AdminLogin, request: Request, response: Response):
        auth.login_limiter.check("a:" + auth.client_ip(request))
        a = db.q1("SELECT * FROM admins WHERE username=?", (body.username.strip(),))
        if not a or not a["active"] or not auth.verify_password(body.password, a["password_hash"]):
            raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")
        db.ex("UPDATE admins SET last_login=? WHERE id=?", (db.now(), a["id"]))
        ttl = settings.admin_session_hours * 3600
        set_cookie(response, auth.ADMIN_COOKIE, auth.make_token("admin", a["id"], ttl, auth.pw_version(a["password_hash"])), ttl)
        return {"id": a["id"], "username": a["username"], "display_name": a["display_name"]}

    @app.post("/api/admin/logout")
    def admin_logout(response: Response):
        response.delete_cookie(auth.ADMIN_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/admin/me")
    def admin_me(a=Depends(auth.current_admin)):
        return {"id": a["id"], "username": a["username"], "display_name": a["display_name"]}

    @app.post("/api/admin/me/password")
    def admin_change_pw(body: PasswordChange, response: Response, a=Depends(auth.current_admin)):
        if not auth.verify_password(body.current_password, a["password_hash"]):
            raise HTTPException(400, "Mật khẩu hiện tại không đúng")
        h = auth.hash_password(body.new_password)
        db.ex("UPDATE admins SET password_hash=? WHERE id=?", (h, a["id"]))
        ttl = settings.admin_session_hours * 3600
        set_cookie(response, auth.ADMIN_COOKIE, auth.make_token("admin", a["id"], ttl, auth.pw_version(h)), ttl)
        return {"ok": True}

    @app.get("/api/admin/stats")
    def stats(a=Depends(auth.current_admin)):
        return {
            "users": db.q1("SELECT COUNT(*) n FROM users")["n"],
            "conversations": db.q1("SELECT COUNT(*) n FROM conversations")["n"],
            "needs_human": db.q1("SELECT COUNT(*) n FROM conversations WHERE needs_human=1")["n"],
            "documents": db.q1("SELECT COUNT(*) n FROM documents")["n"],
            "chunks": len(rag.index.chunks), "llm": settings.llm_ready,
        }

    # ----- Quản trị: tài khoản quản trị -----
    @app.get("/api/admin/admins")
    def list_admins(a=Depends(auth.current_admin)):
        return db.q("SELECT id, username, display_name, active, created_at, last_login FROM admins ORDER BY id")

    @app.post("/api/admin/admins")
    def create_admin(body: AdminIn, a=Depends(auth.current_admin)):
        if db.q1("SELECT id FROM admins WHERE username=?", (body.username,)):
            raise HTTPException(409, "Tên đăng nhập đã tồn tại")
        nid = db.ex("INSERT INTO admins(username, display_name, password_hash, created_at) VALUES(?,?,?,?)",
                    (body.username, body.display_name, auth.hash_password(body.password), db.now()))
        return {"id": nid}

    @app.patch("/api/admin/admins/{aid}")
    def patch_admin(aid: int, body: AdminPatch, a=Depends(auth.current_admin)):
        target = db.q1("SELECT * FROM admins WHERE id=?", (aid,))
        if not target:
            raise HTTPException(404, "Không tìm thấy quản trị viên")
        if body.active is False and aid == a["id"]:
            raise HTTPException(400, "Không thể tự khóa tài khoản của mình")
        if body.display_name is not None:
            db.ex("UPDATE admins SET display_name=? WHERE id=?", (body.display_name, aid))
        if body.password:
            db.ex("UPDATE admins SET password_hash=? WHERE id=?", (auth.hash_password(body.password), aid))
        if body.active is not None:
            db.ex("UPDATE admins SET active=? WHERE id=?", (int(body.active), aid))
        return {"ok": True}

    @app.delete("/api/admin/admins/{aid}")
    def delete_admin(aid: int, a=Depends(auth.current_admin)):
        if aid == a["id"]:
            raise HTTPException(400, "Không thể tự xóa tài khoản của mình")
        if db.q1("SELECT COUNT(*) n FROM admins WHERE active=1 AND id<>?", (aid,))["n"] == 0:
            raise HTTPException(400, "Phải còn ít nhất một quản trị viên")
        db.ex("DELETE FROM admins WHERE id=?", (aid,))
        return {"ok": True}

    # ----- Quản trị: người dùng -----
    @app.get("/api/admin/users")
    def list_users(q: str = "", a=Depends(auth.current_admin)):
        like = f"%{q.strip()}%"
        return db.q("""SELECT u.*, (SELECT COUNT(*) FROM conversations c WHERE c.user_id=u.id) conv_count
                       FROM users u WHERE u.zalo_name LIKE ? OR u.phone LIKE ? ORDER BY COALESCE(u.last_seen, u.created_at) DESC LIMIT 500""",
                    (like, like))

    @app.post("/api/admin/users")
    def create_user(body: UserIn, a=Depends(auth.current_admin)):
        phone = auth.normalize_phone(body.phone)
        if not phone:
            raise HTTPException(400, "Số điện thoại không hợp lệ")
        if db.q1("SELECT id FROM users WHERE phone=?", (phone,)):
            raise HTTPException(409, "Số điện thoại đã có tài khoản")
        nid = db.ex("INSERT INTO users(zalo_name, phone, note, created_at) VALUES(?,?,?,?)",
                    (body.zalo_name.strip(), phone, body.note, db.now()))
        return {"id": nid}

    @app.patch("/api/admin/users/{uid}")
    def patch_user(uid: int, body: UserPatch, a=Depends(auth.current_admin)):
        if not db.q1("SELECT id FROM users WHERE id=?", (uid,)):
            raise HTTPException(404, "Không tìm thấy người dùng")
        if body.phone is not None:
            phone = auth.normalize_phone(body.phone)
            if not phone:
                raise HTTPException(400, "Số điện thoại không hợp lệ")
            if db.q1("SELECT id FROM users WHERE phone=? AND id<>?", (phone, uid)):
                raise HTTPException(409, "Số điện thoại đã thuộc người khác")
            db.ex("UPDATE users SET phone=? WHERE id=?", (phone, uid))
        if body.zalo_name is not None:
            db.ex("UPDATE users SET zalo_name=? WHERE id=?", (body.zalo_name.strip(), uid))
        if body.note is not None:
            db.ex("UPDATE users SET note=? WHERE id=?", (body.note, uid))
        if body.blocked is not None:
            db.ex("UPDATE users SET blocked=? WHERE id=?", (int(body.blocked), uid))
        return {"ok": True}

    @app.delete("/api/admin/users/{uid}")
    def delete_user(uid: int, a=Depends(auth.current_admin)):
        db.ex("DELETE FROM users WHERE id=?", (uid,))
        return {"ok": True}

    # ----- Quản trị: cuộc trò chuyện -----
    @app.get("/api/admin/conversations")
    def list_convs(filter: str = "all", q: str = "", user_id: int = 0, a=Depends(auth.current_admin)):
        cfg = db.get_settings()
        where, params = ["1=1"], []
        if filter == "human":
            where.append("c.needs_human=1")
        if user_id:
            where.append("c.user_id=?"); params.append(user_id)
        if q.strip():
            where.append("(u.zalo_name LIKE ? OR u.phone LIKE ? OR c.title LIKE ? OR c.last_text LIKE ?)")
            params += [f"%{q.strip()}%"] * 4
        rows = db.q(f"""SELECT c.*, u.zalo_name, u.phone FROM conversations c JOIN users u ON u.id=c.user_id
                        WHERE {' AND '.join(where)} ORDER BY c.updated_at DESC LIMIT 300""", params)
        out = [conv_out(c, cfg) for c in rows]
        if filter == "off":
            out = [c for c in out if not c["ai_active"]]
        return out

    def get_conv(cid: int) -> dict:
        c = db.q1("SELECT c.*, u.zalo_name, u.phone FROM conversations c JOIN users u ON u.id=c.user_id WHERE c.id=?", (cid,))
        if not c:
            raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
        return c

    @app.get("/api/admin/conversations/{cid}/messages")
    def admin_messages(cid: int, after: int = 0, a=Depends(auth.current_admin)):
        c = get_conv(cid)
        rows = db.q("""SELECT m.*, ad.display_name admin_name FROM messages m LEFT JOIN admins ad ON ad.id=m.admin_id
                       WHERE m.conversation_id=? AND m.id>? ORDER BY m.id LIMIT 1000""", (cid, after))
        return {"conversation": conv_out(c, db.get_settings()), "messages": [msg_out(m) for m in rows]}

    @app.patch("/api/admin/conversations/{cid}")
    def patch_conv(cid: int, body: ConvPatch, a=Depends(auth.current_admin)):
        get_conv(cid)
        if body.ai_mode is not None:
            db.ex("UPDATE conversations SET ai_mode=? WHERE id=?", (body.ai_mode, cid))
        if body.needs_human is not None:
            db.ex("UPDATE conversations SET needs_human=? WHERE id=?", (int(body.needs_human), cid))
        if body.title is not None:
            db.ex("UPDATE conversations SET title=? WHERE id=?", (body.title, cid))
        return conv_out(get_conv(cid), db.get_settings())

    @app.delete("/api/admin/conversations/{cid}")
    def delete_conv(cid: int, a=Depends(auth.current_admin)):
        db.ex("DELETE FROM conversations WHERE id=?", (cid,))
        return {"ok": True}

    @app.post("/api/admin/conversations/{cid}/reply")
    def reply(cid: int, body: ReplyIn, a=Depends(auth.current_admin)):
        c = get_conv(cid)
        cfg = db.get_settings()
        valid = set(rag.index.by_id)
        ids = rag.cited_ids(body.text, valid)
        m = add_message(cid, "agent", body.text.strip(), sources=ids, admin_id=a["id"], needs_human=False,
                        ai_mode="off" if ai_on(c, cfg) else None)
        m["admin_name"] = a["display_name"] or a["username"]
        return {"message": msg_out(m), "ai_turned_off": ai_on(c, cfg)}

    @app.post("/api/admin/conversations/{cid}/suggest")
    def suggest(cid: int, a=Depends(auth.current_admin)):
        get_conv(cid)
        cfg = db.get_settings()
        msgs = db.q("SELECT role, text FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 8", (cid,))[::-1]
        last_user = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i]["role"] == "user"), None)
        if last_user is None:
            raise HTTPException(400, "Chưa có câu hỏi để gợi ý")
        question, history = msgs[last_user]["text"], msgs[:last_user]
        r = rag.retrieve(question, history, cfg)
        if not r["ok"]:
            return {"ok": False, "reason": "Tài liệu không đủ căn cứ để gợi ý. Hãy trả lời tay."}
        if not settings.llm_ready:
            return {"ok": False, "reason": "Chưa cấu hình Claude API trên server."}
        valid = {c["id"] for c in r["ctx"]}
        try:
            text = "".join(rag.stream(rag.SYSTEM_PROMPT, [{"role": "user", "content": rag.build_user_prompt(question, r["ctx"], history)}]))
        except rag.LLMError:
            return {"ok": False, "reason": "Không gọi được Claude API, thử lại sau."}
        if rag.SENTINEL in text or not rag.cited_ids(text, valid):
            return {"ok": False, "reason": "Tài liệu không đủ căn cứ để gợi ý. Hãy trả lời tay."}
        return {"ok": True, "text": rag.clean_citations(text, valid).strip()}

    @app.patch("/api/admin/messages/{mid}")
    def edit_message(mid: int, body: ReplyIn, a=Depends(auth.current_admin)):
        m = db.q1("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise HTTPException(404, "Không tìm thấy tin nhắn")
        if m["role"] == "user":
            raise HTTPException(400, "Không sửa tin nhắn của người dùng")
        db.ex("UPDATE messages SET text=?, sources=?, edited_at=? WHERE id=?",
              (body.text.strip(), json.dumps(rag.cited_ids(body.text, set(rag.index.by_id))), db.now(), mid))
        return {"ok": True}

    @app.delete("/api/admin/messages/{mid}")
    def delete_message(mid: int, a=Depends(auth.current_admin)):
        db.ex("DELETE FROM messages WHERE id=?", (mid,))
        return {"ok": True}

    # ----- Quản trị: kiến thức -----
    @app.get("/api/admin/documents")
    def list_docs(a=Depends(auth.current_admin)):
        return db.q("SELECT * FROM documents ORDER BY updated_at DESC")

    @app.post("/api/admin/documents")
    async def upload_docs(files: list[UploadFile] = File(...), doc_id: str = Form(""), title: str = Form(""),
                          scope: str = Form("chung"), version: int = Form(1), a=Depends(auth.current_admin)):
        results, errors = [], []
        single = len(files) == 1
        for f in files:
            data = await f.read()
            if len(data) > settings.max_upload_mb * 1024 * 1024:
                errors.append({"file": f.filename, "error": f"File lớn hơn {settings.max_upload_mb} MB"})
                continue
            base = (f.filename or "tai-lieu").rsplit(".", 1)[0]
            did = rag.slug(doc_id if single and doc_id.strip() else base)
            ttl = (title.strip() if single and title.strip() else base)[:200]
            try:
                results.append(ingest_document(f.filename or "", data, did, ttl, (scope.strip() or "chung")[:40], max(1, version)))
            except Exception as e:  # noqa: BLE001 - báo lỗi từng file cho quản trị viên
                errors.append({"file": f.filename, "error": str(e)})
        return {"ok": results, "errors": errors}

    @app.get("/api/admin/documents/{doc_id}/chunks")
    def doc_chunks(doc_id: str, a=Depends(auth.current_admin)):
        return db.q("SELECT id, idx, text FROM chunks WHERE doc_id=? ORDER BY idx", (doc_id,))

    @app.delete("/api/admin/documents/{doc_id}")
    def delete_doc(doc_id: str, a=Depends(auth.current_admin)):
        db.ex("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        rag.index.rebuild()
        return {"ok": True}

    # ----- Quản trị: cài đặt -----
    @app.get("/api/admin/settings")
    def get_cfg(a=Depends(auth.current_admin)):
        return db.get_settings()

    @app.put("/api/admin/settings")
    def put_cfg(body: dict, a=Depends(auth.current_admin)):
        if "threshold" in body:
            body["threshold"] = min(1.0, max(0.0, float(body["threshold"])))
        if "top_k" in body:
            body["top_k"] = min(12, max(1, int(body["top_k"])))
        if "suggest" in body and isinstance(body["suggest"], str):
            body["suggest"] = [s.strip() for s in body["suggest"].splitlines() if s.strip()][:6]
        return db.save_settings(body)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return app
