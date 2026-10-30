"""Unit tests for app/mcp.py: bearer-auth middleware and the privacy-gated tools."""
import hashlib
import json


import pytest
from sqlalchemy.orm import sessionmaker
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient


from app import mcp as mcp_module
from app.db import MCPAccessLog, MCPApproval, Trait, User




@pytest.fixture(autouse=True)
def approval_identity(user, db_session):
    user.mcp_token_hash = "test-token-hash"
    db_session.commit()
    token = mcp_module.current_mcp_token_hash.set(user.mcp_token_hash)
    yield
    mcp_module.current_mcp_token_hash.reset(token)




def _bind_to_test_db(monkeypatch, db_session):
    """app/mcp.py opens its own SessionLocal() per call - point it at the same
    in-memory engine the test fixture uses."""
    test_session_factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(mcp_module, "SessionLocal", test_session_factory)




# ---- tools / privacy gate ----




def test_get_profile_summary_only_returns_share_tier(db_session, user, monkeypatch):
    _bind_to_test_db(monkeypatch, db_session)
    db_session.add_all(
        [
            Trait(user_id=user.id, category="food", key="k1", value="Loves ramen", confidence=0.9, privacy_tier="share"),
            Trait(user_id=user.id, category="routine", key="k2", value="Home address X", confidence=0.9, privacy_tier="private"),
        ]
    )
    db_session.commit()


    token = mcp_module.current_mcp_user_id.set(user.id)
    try:
        result = mcp_module.get_profile_summary(categories=None, ctx=None)
    finally:
        mcp_module.current_mcp_user_id.reset(token)


    assert "ramen" in result
    assert "address" not in result


    log = db_session.query(MCPAccessLog).one()
    assert log.traits_returned == 1
    assert log.traits_blocked == 1
    assert log.tool == "get_profile_summary"




def test_request_private_info_never_returns_trait_values(db_session, user, monkeypatch):
    _bind_to_test_db(monkeypatch, db_session)
    db_session.add(
        Trait(user_id=user.id, category="routine", key="k1", value="Secret address", confidence=0.9, privacy_tier="private")
    )
    db_session.commit()


    token = mcp_module.current_mcp_user_id.set(user.id)
    try:
        result = mcp_module.request_private_info(category="routine", ctx=None)
    finally:
        mcp_module.current_mcp_user_id.reset(token)


    assert "Secret address" not in str(result)
    assert result["status"] == "blocked"
    log = db_session.query(MCPAccessLog).one()
    assert log.tool == "request_private_info"
    assert log.traits_returned == 0
    assert log.traits_blocked == 1




@pytest.fixture()
def approval_request(db_session, user, monkeypatch):
    _bind_to_test_db(monkeypatch, db_session)
    db_session.add_all([
        Trait(user_id=user.id, category="fitness", key="running", value="Runs weekends", privacy_tier="ask"),
        Trait(user_id=user.id, category="fitness", key="secret", value="Private medical detail", privacy_tier="private"),
    ])
    db_session.commit()
    token = mcp_module.current_mcp_user_id.set(user.id)
    try:
        yield mcp_module.request_private_info("fitness")
    finally:
        mcp_module.current_mcp_user_id.reset(token)




def test_approval_allow_once_exact_snapshot(client, db_session, approval_request):
    request = approval_request
    assert request["status"] == "pending" and "Runs weekends" not in str(request)
    check = lambda: mcp_module.check_approval(request["request_id"], request["receipt"])
    assert check()["status"] == "pending"
    listed = client.get("/api/mcp/approvals").json()
    assert len(listed[0]["traits"]) == 1
    assert "Private medical detail" not in str(listed)
    assert "receipt" not in str(listed) and "token_hash" not in str(listed)
    assert client.post(f"/api/mcp/approvals/{request['request_id']}/decision", json={"decision":"allow"}).status_code == 200
    released = check()
    assert released["status"] == "released"
    assert released["traits"][0]["value"] == "Runs weekends"
    assert check()["status"] == "consumed"
    assert client.post(f"/api/mcp/approvals/{request['request_id']}/decision", json={"decision":"allow"}).status_code == 409
    assert db_session.query(Trait).filter_by(key="running").one().privacy_tier == "ask"




@pytest.mark.parametrize("action,expected", [
    ("deny", "denied"), ("expire", "expired"), ("private", "invalidated"),
    ("edit", "invalidated"), ("delete", "invalidated"), ("rotate", "invalidated"),
])
def test_approval_never_releases_after_changes(client, db_session, user, approval_request, action, expected):
    from datetime import datetime, timedelta, timezone
    request = approval_request
    row = db_session.get(MCPApproval, request["request_id"])
    if action == "deny":
        client.post(f"/api/mcp/approvals/{row.id}/decision", json={"decision":"deny"})
    elif action == "expire":
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif action == "rotate":
        user.mcp_token_hash = "rotated"
    else:
        trait = db_session.query(Trait).filter_by(key="running").one()
        if action == "private":
            trait.privacy_tier = "private"
        elif action == "edit":
            trait.value = "Changed after request"
        else:
            db_session.delete(trait)
    db_session.commit()
    result = mcp_module.check_approval(row.id, request["receipt"])
    assert result["status"] == expected and "traits" not in result
    assert client.post(f"/api/mcp/approvals/{row.id}/decision", json={"decision":"allow"}).status_code == 409




def test_approval_credentials_and_owner_isolation(client, db_session, user, approval_request):
    request = approval_request
    with pytest.raises(PermissionError):
        mcp_module.check_approval(request["request_id"], "wrong receipt")
    with pytest.raises(PermissionError):
        mcp_module.check_approval("missing", request["receipt"])
    other = User(email="isolated@example.com")
    db_session.add(other)
    db_session.commit()
    token = mcp_module.current_mcp_user_id.set(other.id)
    try:
        with pytest.raises(PermissionError):
            mcp_module.check_approval(request["request_id"], request["receipt"])
    finally:
        mcp_module.current_mcp_user_id.reset(token)
    from app.main import app
    from app.routes import get_current_user
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.get("/api/mcp/approvals").json() == []
    assert client.post(f"/api/mcp/approvals/{request['request_id']}/decision", json={"decision":"deny"}).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.post(f"/api/mcp/approvals/{request['request_id']}/decision", json={"decision":"invalid"}).status_code == 422
    client.delete("/api/account/data")
    assert db_session.query(MCPApproval).count() == 0




def test_approval_edit_after_allow_invalidates_grant(client, db_session, approval_request):
    request = approval_request
    client.post(f"/api/mcp/approvals/{request['request_id']}/decision", json={"decision":"allow"})
    trait = db_session.query(Trait).filter_by(key="running").one()
    trait.privacy_tier = "private"
    db_session.commit()
    assert mcp_module.check_approval(request["request_id"], request["receipt"])["status"] == "invalidated"




def test_approval_expires_even_after_allow(client, db_session, approval_request):
    from datetime import datetime, timedelta, timezone
    request = approval_request
    client.post(f"/api/mcp/approvals/{request['request_id']}/decision", json={"decision":"allow"})
    row = db_session.get(MCPApproval, request["request_id"])
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    result = mcp_module.check_approval(request["request_id"], request["receipt"])
    assert result["status"] == "expired" and "traits" not in result




def test_approval_receipt_cannot_be_used_with_different_bearer(approval_request):
    token = mcp_module.current_mcp_token_hash.set("different-client-credential")
    try:
        with pytest.raises(PermissionError):
            mcp_module.check_approval(approval_request["request_id"], approval_request["receipt"])
    finally:
        mcp_module.current_mcp_token_hash.reset(token)




def test_request_requires_bearer_and_valid_category():
    token = mcp_module.current_mcp_user_id.set("user")
    auth = mcp_module.current_mcp_token_hash.set(None)
    try:
        with pytest.raises(PermissionError):
            mcp_module.request_private_info("fitness")
        mcp_module.current_mcp_token_hash.set("hash")
        with pytest.raises(ValueError):
            mcp_module.request_private_info("my_life")
    finally:
        mcp_module.current_mcp_user_id.reset(token)
        mcp_module.current_mcp_token_hash.reset(auth)




def test_tools_raise_without_authenticated_user():
    with pytest.raises(PermissionError):
        mcp_module.get_profile_summary(categories=None, ctx=None)




# ---- bearer-auth middleware ----




async def _inner_app(scope, receive, send):
    user_id = mcp_module.current_mcp_user_id.get()
    await PlainTextResponse(f"authenticated as {user_id}")(scope, receive, send)




def _make_client(db_session, monkeypatch):
    _bind_to_test_db(monkeypatch, db_session)
    return TestClient(mcp_module.BearerAuthMiddleware(_inner_app))




def test_rejects_request_without_bearer_token(db_session, monkeypatch):
    client = _make_client(db_session, monkeypatch)
    response = client.get("/mcp")
    assert response.status_code == 401




def test_rejects_invalid_bearer_token(db_session, monkeypatch):
    client = _make_client(db_session, monkeypatch)
    response = client.get("/mcp", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401




def test_accepts_valid_bearer_token_and_sets_user_context(db_session, user, monkeypatch):
    token = "my-plaintext-token"
    user.mcp_token_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()


    client = _make_client(db_session, monkeypatch)
    response = client.get("/mcp", headers={"Authorization": f"Bearer {token}"})


    assert response.status_code == 200
    assert user.id in response.text




def test_streamable_http_approval_flow(client, db_session, user, monkeypatch):
    _bind_to_test_db(monkeypatch, db_session)
    bearer = "local-test-credential"
    user.mcp_token_hash = hashlib.sha256(bearer.encode()).hexdigest()
    db_session.add(Trait(user_id=user.id, category="fitness", key="walk", value="Weekend walks", privacy_tier="ask"))
    db_session.commit()
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json, text/event-stream"}


    def rpc(transport, method, params, number):
        response = transport.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": number, "method": method, "params": params,
        })
        assert response.status_code == 200, response.text
        if "mcp-session-id" in response.headers:
            headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            result = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
        else:
            result = response.json()
        assert "error" not in result, result
        return result["result"]


    with TestClient(mcp_module.build_mcp_asgi_app(), base_url="http://localhost:8800") as transport:
        rpc(transport, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "Local approval test", "version": "1"},
        }, 1)
        response = transport.post("/mcp", headers=headers, json={"jsonrpc":"2.0", "method":"notifications/initialized"})
        assert response.status_code == 202
        tools = rpc(transport, "tools/list", {}, 3)
        assert "check_approval" in {tool["name"] for tool in tools["tools"]}
        result = rpc(transport, "tools/call", {
            "name": "request_private_info", "arguments": {"category":"fitness"},
        }, 4)
        assert not result.get("isError"), result
        request = json.loads(result["content"][0]["text"])
        assert request["status"] == "pending"
        assert "Weekend walks" not in str(result)
        assert client.get("/api/mcp/approvals").json()[0]["client_name"] == "Local approval test"
        assert client.post(f"/api/mcp/approvals/{request['request_id']}/decision", json={"decision":"allow"}).status_code == 200
        result = rpc(transport, "tools/call", {
            "name": "check_approval", "arguments": {"request_id":request["request_id"], "receipt":request["receipt"]},
        }, 5)
        assert not result.get("isError"), result
        assert json.loads(result["content"][0]["text"])["status"] == "released"



