import json
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "matkhau123")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("LLM_ENABLED", "0")
    import importlib
    from app import config
    importlib.reload(config)
    from app import db, auth, rag, main
    for m in (db, auth, rag, main):
        importlib.reload(m)
    db.reset_for_tests()
    return TestClient(main.create_app())


def admin_login(c):
    r = c.post("/api/admin/login", json={"username": "admin", "password": "matkhau123"})
    assert r.status_code == 200


DOC = ("Quy trình đăng ký AppID\n\nBước 1: Merchant gửi hồ sơ gồm giấy phép kinh doanh và CCCD người đại diện.\n\n"
       "Bước 2: OP thẩm định website và kiểm tra tên miền.").encode()


def chat(c, text, cid=None):
    r = c.post("/api/chat", json={"conversation_id": cid, "text": text})
    assert r.status_code == 200, r.text
    return [json.loads(x) for x in r.text.strip().splitlines()]


def test_pages(client):
    assert client.get("/user/").status_code == 200
    assert client.get("/", follow_redirects=False).headers["location"] == "/user/"
    assert client.get("/admin/").status_code == 200
    assert client.get("/health").json()["ok"]


def test_user_login_validation(client):
    assert client.post("/api/auth/login", json={"zalo_name": "Lan", "phone": "123"}).status_code == 400
    r = client.post("/api/auth/login", json={"zalo_name": "Lan", "phone": "+84 901 234 567"})
    assert r.status_code == 200 and r.json()["phone"] == "0901234567" and r.json()["new"]
    r = client.post("/api/auth/login", json={"zalo_name": "Lan Nguyễn", "phone": "0901234567"})
    assert not r.json()["new"]
    assert client.get("/api/me").json()["zalo_name"] == "Lan Nguyễn"


def test_admin_required(client):
    assert client.get("/api/admin/users").status_code == 401
    assert client.post("/api/admin/login", json={"username": "admin", "password": "sai"}).status_code == 401


def test_full_flow(client):
    admin_login(client)
    r = client.post("/api/admin/documents", files=[("files", ("appid.txt", DOC, "text/plain"))], data={"scope": "BD", "version": "1"})
    assert r.json()["ok"][0]["chunks"] >= 1
    # Người dùng hỏi: không có key Claude nên trả đoạn liên quan nhất
    client.post("/api/auth/login", json={"zalo_name": "Lan", "phone": "0901234567"})
    evs = chat(client, "đăng ký appid cần giấy tờ gì")
    msg = [e for e in evs if e["type"] == "message"][-1]["message"]
    assert msg["fallback"] and msg["sources"][0]["id"] == "appid-v1-c000"
    cid = evs[0]["conversation"]["id"]
    # Câu hỏi ngoài tài liệu: chuyển nhân viên
    evs = chat(client, "thời tiết hôm nay", cid)
    assert [e for e in evs if e["type"] == "message"][-1]["message"]["escalated"]
    # Quản trị viên thấy, trả lời tay, AI tự tắt
    convs = client.get("/api/admin/conversations?filter=human").json()
    assert convs and convs[0]["needs_human"]
    r = client.post(f"/api/admin/conversations/{cid}/reply", json={"text": "Mình hỗ trợ bạn nhé"}).json()
    assert r["ai_turned_off"]
    evs = chat(client, "cảm ơn", cid)
    assert any(e["type"] == "waiting" for e in evs)
    msgs = client.get(f"/api/conversations/{cid}/messages").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "ai", "user", "ai", "agent", "user"]
    # Người dùng khác không xem được
    client.post("/api/auth/login", json={"zalo_name": "Bình", "phone": "0912345678"})
    assert client.get(f"/api/conversations/{cid}/messages").status_code == 404


def test_user_admin_crud(client):
    admin_login(client)
    uid = client.post("/api/admin/users", json={"zalo_name": "An", "phone": "0987654321"}).json()["id"]
    assert client.post("/api/admin/users", json={"zalo_name": "An", "phone": "0987654321"}).status_code == 409
    client.patch(f"/api/admin/users/{uid}", json={"blocked": True})
    assert client.post("/api/auth/login", json={"zalo_name": "An", "phone": "0987654321"}).status_code == 403
    assert client.delete(f"/api/admin/users/{uid}").status_code == 200
    aid = client.post("/api/admin/admins", json={"username": "op2", "password": "matkhau456"}).json()["id"]
    me = client.get("/api/admin/me").json()["id"]
    assert client.delete(f"/api/admin/admins/{me}").status_code == 400
    assert client.delete(f"/api/admin/admins/{aid}").status_code == 200


def test_password_change_invalidates(client):
    admin_login(client)
    old = client.cookies.get("kb_admin")
    assert client.post("/api/admin/me/password", json={"current_password": "matkhau123", "new_password": "moi12345"}).status_code == 200
    client.cookies.set("kb_admin", old)
    assert client.get("/api/admin/me").status_code == 401


def test_llm_answer_with_citations(client, monkeypatch):
    from app import main, rag
    from app.config import settings
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "test")
    monkeypatch.setattr(rag, "stream", lambda system, messages: iter(["Cần giấy phép kinh doanh ", "[appid-v1-c000] [bia-v9-c999]."]))
    admin_login(client)
    client.post("/api/admin/documents", files=[("files", ("appid.txt", DOC, "text/plain"))])
    client.post("/api/auth/login", json={"zalo_name": "Lan", "phone": "0901234567"})
    evs = chat(client, "đăng ký appid cần giấy tờ gì")
    assert any(e["type"] == "delta" for e in evs)
    msg = [e for e in evs if e["type"] == "message"][-1]["message"]
    assert not msg["escalated"] and "[bia-v9-c999]" not in msg["text"] and msg["sources"][0]["id"] == "appid-v1-c000"
