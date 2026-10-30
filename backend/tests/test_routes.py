"""Route-level tests using FastAPI's TestClient with get_db/get_current_user
overridden to the test fixtures. Nemotron-calling functions are patched at the
import site inside each route module so no real API key is needed.
"""
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
import hashlib

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.connectors import ConnectorError, NormalizedRawItem, TokenBundle
from app.db import Connection, EmailProcessingLease, Fact, GmailAccount, ModelCall, OAuthState, RawItem, Trait, User
from app.routes import QuestionResult
from app.services import decrypt_token, encrypt_token


# ---- connections ----

@pytest.fixture()
def public_demo_config(monkeypatch):
    from app import main, routes
    settings = Settings(_env_file=None, public_demo=True, frontend_url="https://nemo.example",
                        app_env="production",nebius_api_key="isolated-demo-test-key")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    main._demo_ai_requests.clear()
    return settings


@pytest.mark.parametrize("method,path,body", [
    ("GET","/api/connect/gmail",None), ("GET","/api/connect/gcal",None),
    ("GET","/api/oauth/callback/gmail",None), ("POST","/api/sync/gmail",None),
    ("POST","/api/today/process",{"consent":True,"timezone_name":"UTC"}),
    ("PUT","/api/profile/my-life",{"home_city":"private place"}),
    ("POST","/api/mcp/token",None), ("DELETE","/api/account/data",None),
    ("POST","/api/qa/start",{"topic":"food"}), ("PATCH","/api/traits/any",{"value":"changed"}),
])
def test_public_demo_blocks_private_mutations(client, public_demo_config, method, path, body):
    with patch("app.routes.GmailConnector.authorize_url") as oauth, patch("app.routes.call_llm") as model:
        response = client.request(method,path,json=body)
    assert response.status_code == 403 and "sample" in response.json()["detail"].lower()
    oauth.assert_not_called()
    model.assert_not_called()


def test_public_demo_chat_is_ephemeral_and_preserves_stream(client, public_demo_config, db_session):
    from app.db import ChatMessage
    from app.main import _demo_ai_lock
    def stream(*args, **kwargs):
        assert _demo_ai_lock.locked()
        yield "Public sample answer."
    with patch("app.routes.call_llm_stream", side_effect=stream):
        response = client.post("/api/chat",json={"message":"Do not retain this question"})
    assert response.status_code == 200 and response.text == "Public sample answer."
    assert db_session.query(ChatMessage).count() == 0
    assert not _demo_ai_lock.locked()


def test_public_demo_rate_limit_and_single_flight(client, public_demo_config):
    from app.main import _demo_ai_lock
    _demo_ai_lock.acquire()
    try:
        assert client.post("/api/chat/compare",json={"message":"Question"}).status_code == 429
    finally:
        _demo_ai_lock.release()
    with patch("app.routes.call_llm", return_value="Sample answer.") as model:
        for _ in range(10):
            assert client.post("/api/chat/compare",json={"message":"Question"}).status_code == 200
        assert client.post("/api/chat/compare",json={"message":"Question"}).status_code == 429
        assert model.call_count == 20


def test_public_demo_request_size_and_failed_calls_release_lock(client, public_demo_config):
    from app.main import _demo_ai_lock
    from app.pipeline import EmailProcessingError
    assert client.post("/api/chat",json={"message":"x"*17000}).status_code == 413
    assert client.post("/api/chat",content=b"{}",headers={"content-length":"bad"}).status_code == 400
    with patch("app.routes.call_llm",side_effect=EmailProcessingError("Synthetic failure")):
        assert client.post("/api/chat/compare",json={"message":"Question"}).status_code == 502
    assert not _demo_ai_lock.locked()


def test_public_demo_startup_seeds_without_models(public_demo_config, user):
    from app import main
    with patch.object(main, "init_db") as init, patch.object(main, "SessionLocal") as session, patch.object(
        main, "get_or_create_demo_user", return_value=user,
    ), patch.object(main, "load_all_demo_data") as seed:
        main.on_startup()
    init.assert_called_once()
    seed.assert_called_once_with(session.return_value.__enter__.return_value, user.id)


def test_production_requires_sample_mode_and_local_startup_is_unchanged(monkeypatch):
    from app import main
    settings = Settings(_env_file=None,app_env="production")
    monkeypatch.setattr(main,"get_settings",lambda:settings)
    with patch.object(main,"init_db") as init:
        with pytest.raises(RuntimeError,match="authentication"):
            main.on_startup()
        init.assert_not_called()
        settings.app_env = "development"
        with patch.object(main,"load_all_demo_data") as seed:
            main.on_startup()
        init.assert_called_once()
        seed.assert_not_called()


def test_public_demo_rate_window_expires_and_safe_reads_work(client,public_demo_config,monkeypatch):
    from app import main
    main._demo_ai_requests.extend([0.0]*10)
    monkeypatch.setattr(main,"monotonic",lambda:61.0)
    assert client.get("/health").status_code == 200
    assert client.get("/api/profile").status_code == 200
    assert client.post("/api/demo/load").status_code == 200
    with patch("app.routes.call_llm",return_value="Sample answer."):
        assert client.post("/api/chat/compare",json={"message":"Public question"}).status_code == 200
    assert len(main._demo_ai_requests) == 1


def test_public_demo_can_disable_paid_ai_by_removing_key(client,public_demo_config):
    public_demo_config.nebius_api_key = ""
    with patch("app.routes.call_llm") as model:
        response = client.post("/api/chat/compare",json={"message":"Question"})
    assert response.status_code == 503 and "disabled" in response.json()["detail"]
    model.assert_not_called()


def test_deployment_settings_separate_database_and_exact_cors():
    settings = Settings(_env_file=None, public_demo=True, app_env="production",frontend_url="https://nemo.example/")
    assert settings.effective_database_url != settings.database_url
    assert settings.effective_database_url.endswith("personal_nemo_public_demo.db")
    assert settings.cors_origins == ["https://nemo.example"]
    local = Settings(_env_file=None)
    assert local.effective_database_url == local.database_url
    assert "http://127.0.0.1:5173" in local.cors_origins
    for origin in ("*","https://example.org/path","https://user:password@example.org","https://example.org?x=1"):
        with pytest.raises(ValueError):
            Settings(_env_file=None,frontend_url=origin)


def test_list_connections_shows_all_providers_disconnected_by_default(client):
    response = client.get("/api/connections")
    assert response.status_code == 200
    providers = {c["provider"] for c in response.json()}
    assert providers == {"gmail", "gcal", "spotify", "strava", "trakt"}


def test_connect_provider_redirects_to_authorize_url(client):
    response = client.get("/api/connect/spotify", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "accounts.spotify.com" in response.headers["location"]


def test_demo_load_calls_the_demo_loader(client, db_session, user):
    fake_result = {"inserted_by_provider": {"gmail": 5}, "facts_created": 2, "facts_tagged": 2, "traits_upserted": 1}
    with patch("app.routes.load_all_demo_data", return_value=fake_result) as mocked:
        response = client.post("/api/demo/load")
    assert response.status_code == 200
    assert response.json() == fake_result
    mocked.assert_called_once_with(db_session, user.id)


def test_oauth_callback_returns_501_for_unimplemented_live_oauth(client):
    response = client.get("/api/oauth/callback/spotify", params={"code": "abc123"})
    assert response.status_code == 501


def test_sync_provider_reloads_sample_data_and_seeds_profile_with_zero_llm_cost(client, db_session, user):
    fake_seed_result = {"facts_created": 0, "traits_upserted": 0}
    with patch("app.routes.seed_demo_profile_for_user", return_value=fake_seed_result):
        response = client.post("/api/sync/spotify")
    assert response.status_code == 200
    assert response.json()["provider"] == "spotify"
    assert response.json()["raw_items_inserted"] > 0


def test_sync_unknown_provider_returns_404(client):
    response = client.post("/api/sync/not-a-provider")
    assert response.status_code == 404


def start_gmail(client):
    response = client.get("/api/connect/gmail", follow_redirects=False)
    assert response.status_code == 307
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    return query


def start_calendar(client):
    response = client.get("/api/connect/gcal", follow_redirects=False)
    assert response.status_code == 307
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/calendar.readonly"]
    assert query["code_challenge_method"] == ["S256"]
    return query


def complete_calendar(client, calendar_id="calendar@example.com", refresh="calendar-refresh", query=None):
    from app.connectors import CalendarIdentity
    query = query or start_calendar(client)
    token = TokenBundle("calendar-access",refresh,datetime.now(timezone.utc)+timedelta(hours=1))
    with patch("app.routes.GoogleCalendarConnector.exchange_code",return_value=token) as exchange, patch(
        "app.routes.GoogleCalendarConnector.primary_calendar",return_value=CalendarIdentity(id=calendar_id,timeZone="Asia/Kolkata"),
    ):
        response = client.get("/api/oauth/callback/gcal",params={"state":query["state"][0],"code":"calendar-code"},follow_redirects=False)
    return response,query,exchange


def test_calendar_oauth_is_provider_bound_and_keeps_gmail_tokens(client, db_session, user, gmail_config):
    from app.db import CalendarAccount
    complete_gmail(client)
    gmail_before = db_session.query(Connection).filter_by(provider="gmail").one().encrypted_refresh_token
    query = start_calendar(client)
    assert client.get("/api/oauth/callback/gmail", params={"state":query["state"][0],"code":"wrong-provider"}).status_code == 400
    response,query,exchange = complete_calendar(client,calendar_id="mailbox@example.com",query=query)
    assert response.status_code == 303 and response.headers["location"].endswith("gcal=connected")
    assert exchange.call_args.kwargs["code_verifier"]
    assert db_session.get(CalendarAccount,user.id).calendar_id == "mailbox@example.com"
    assert db_session.query(Connection).filter_by(provider="gmail").one().encrypted_refresh_token == gmail_before
    assert decrypt_token(db_session.query(Connection).filter_by(provider="gcal").one().encrypted_refresh_token) == "calendar-refresh"
    assert client.get("/api/oauth/callback/gcal", params={"state":query["state"][0],"code":"replay"}).status_code == 400
    assert db_session.query(ModelCall).count() == 0


@pytest.mark.parametrize("case",["missing-browser","expired","wrong-user","cancelled","provider-error"])
def test_calendar_oauth_binding_expiry_and_consent_errors(client,db_session,user,gmail_config,case):
    query = start_calendar(client)
    flow = db_session.query(OAuthState).one()
    if case == "missing-browser":
        client.cookies.clear()
    elif case == "expired":
        flow.expires_at = datetime.now(timezone.utc)-timedelta(minutes=1)
    elif case == "wrong-user":
        other = User(email="other@example.invalid")
        db_session.add(other)
        db_session.flush()
        flow.user_id = other.id
    db_session.commit()
    params = {"state":query["state"][0],"code":"calendar-code"}
    if case in ("cancelled","provider-error"):
        params["error"] = "access_denied" if case == "cancelled" else "server_error"
    with patch("app.routes.GoogleCalendarConnector.exchange_code") as exchange:
        response = client.get("/api/oauth/callback/gcal",params=params,follow_redirects=False)
    exchange.assert_not_called()
    if case in ("cancelled","provider-error"):
        assert response.status_code == 303
        assert response.headers["location"].endswith("gcal="+("cancelled" if case == "cancelled" else "failed"))
        assert db_session.query(OAuthState).count() == 0
    else:
        assert response.status_code == 400
    assert db_session.query(ModelCall).count() == 0


def test_calendar_reconnect_reuses_only_same_account_refresh_token(client,db_session,gmail_config):
    from app.db import CalendarAccount
    assert complete_calendar(client,refresh=None)[0].headers["location"].endswith("gcal=failed")
    assert db_session.query(CalendarAccount).count() == 0
    assert complete_calendar(client)[0].headers["location"].endswith("gcal=connected")
    assert complete_calendar(client,refresh=None)[0].headers["location"].endswith("gcal=connected")
    assert complete_calendar(client,calendar_id="other@example.com")[0].headers["location"].endswith("gcal=failed")
    assert db_session.query(CalendarAccount).one().calendar_id == "calendar@example.com"
    assert decrypt_token(db_session.query(Connection).filter_by(provider="gcal").one().encrypted_refresh_token) == "calendar-refresh"


@pytest.mark.parametrize("same_account",[True,False])
def test_google_disconnect_handles_shared_grants_and_retains_imports(client,db_session,user,gmail_config,same_account):
    from app.db import CalendarAccount
    complete_gmail(client)
    complete_calendar(client,calendar_id="mailbox@example.com" if same_account else "other@example.com")
    db_session.add(RawItem(user_id=user.id,provider="gcal",external_id="gcal_live:retained",item_type="calendar_event",payload_json={},processed=True))
    db_session.commit()
    with patch("app.routes.GoogleCalendarConnector.revoke") as revoke:
        response = client.delete("/api/connections/gcal")
    assert response.status_code == 200 and db_session.query(RawItem).count() == 1
    revoke.assert_called_once_with("calendar-refresh")
    assert db_session.get(CalendarAccount,user.id) is None
    gmail = db_session.query(Connection).filter_by(provider="gmail").one()
    assert gmail.status == ("disconnected" if same_account else "connected")
    assert bool(gmail.encrypted_refresh_token) == (not same_account)
    assert db_session.query(EmailProcessingLease).count() == 0


def test_calendar_api_learning_consent_removal_and_account_cleanup(client,db_session,user,calendar_sync,calendar_event,calendar_model):
    from app.db import CalendarAccount, CalendarLearning, TodayItem
    calendar_sync([calendar_event()])
    connection = next(item for item in client.get("/api/connections").json() if item["provider"] == "gcal")
    assert connection["account_email"] == "calendar@example.com" and connection["pending_items"] == 1
    assert client.post("/api/gcal/learn",json={"consent":False}).status_code == 422
    assert calendar_model.call_count == 0
    result = client.post("/api/gcal/learn",json={"consent":True})
    assert result.status_code == 200 and result.json()["events_checked"] == 1
    activity = client.get("/api/profile/activity").json()
    assert len(activity["events"]) == 1
    fact_id = activity["events"][0]["fact_id"]
    assert client.delete("/api/profile/activity/"+fact_id).status_code == 200
    assert db_session.query(CalendarLearning).one().status == "removed"
    assert db_session.query(Fact).count() == db_session.query(Trait).count() == 0
    calendar_sync([calendar_event(summary="Badminton changed")])
    assert client.post("/api/gcal/learn",json={"consent":True}).json()["events_checked"] == 0
    assert calendar_model.call_count == 1 and db_session.query(TodayItem).count() == 1
    assert client.delete("/api/account/data").status_code == 200
    assert db_session.query(CalendarAccount).count() == db_session.query(CalendarLearning).count() == db_session.query(TodayItem).count() == 0


def test_calendar_sync_requires_connection_and_shared_lease(client,db_session,user,gmail_config):
    from app.db import EmailProcessingLease
    assert client.post("/api/sync/gcal").status_code == 409
    assert db_session.query(RawItem).count() == 0
    db_session.add(EmailProcessingLease(user_id=user.id,owner="other-request",expires_at=datetime.now(timezone.utc)+timedelta(minutes=10)))
    db_session.commit()
    with patch("app.routes.sync_calendar") as sync:
        assert client.post("/api/sync/gcal").status_code == 409
        assert client.delete("/api/connections/gcal").status_code == 409
        assert client.post("/api/gcal/learn",json={"consent":True}).status_code == 409
    sync.assert_not_called()
    assert db_session.query(EmailProcessingLease).one().owner == "other-request"


def complete_gmail(client, *, refresh="refresh"):
    query = start_gmail(client)
    token = TokenBundle("access", refresh, datetime.now(timezone.utc) + timedelta(hours=1))
    with patch("app.routes.GmailConnector.exchange_code", return_value=token) as exchange, patch(
        "app.routes.GmailConnector.mailbox_email", return_value="mailbox@example.com"
    ):
        response = client.get("/api/oauth/callback/gmail", params={"state":query["state"][0], "code":"auth-code"}, follow_redirects=False)
    return response, query, exchange


def test_gmail_oauth_stores_encrypted_tokens_and_consumes_state(client, db_session, user, gmail_config):
    response, query, exchange = complete_gmail(client)
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:5173/?gmail=connected"
    assert response.headers["cache-control"] == "no-store"
    verifier = exchange.call_args.kwargs["code_verifier"]
    import base64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == query["code_challenge"][0]
    connection = db_session.query(Connection).filter_by(provider="gmail").one()
    assert connection.encrypted_access_token != "access"
    assert decrypt_token(connection.encrypted_refresh_token) == "refresh"
    assert connection.status == "connected" and connection.last_synced_at is None
    assert db_session.get(GmailAccount, user.id).email == "mailbox@example.com"
    assert db_session.query(OAuthState).count() == 0
    assert client.get("/api/oauth/callback/gmail", params={"state":query["state"][0], "code":"replay"}).status_code == 400
    assert db_session.query(ModelCall).count() == 0


@pytest.mark.parametrize("failure", ["wrong-state", "missing-cookie", "expired", "wrong-user"])
def test_gmail_oauth_rejects_unbound_or_expired_callback(client, db_session, user, gmail_config, failure):
    query = start_gmail(client)
    state = query["state"][0]
    if failure == "wrong-state":
        state = "not-valid"
    elif failure == "missing-cookie":
        client.cookies.clear()
    elif failure == "expired":
        db_session.query(OAuthState).one().expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
    else:
        other = User(email="other-oauth@example.com")
        db_session.add(other)
        db_session.flush()
        db_session.query(OAuthState).one().user_id = other.id
        db_session.commit()
    with patch("app.routes.GmailConnector.exchange_code") as exchange:
        response = client.get("/api/oauth/callback/gmail", params={"state":state, "code":"code"})
    assert response.status_code == 400
    exchange.assert_not_called()


@pytest.mark.parametrize("error", ["access_denied", "server_error"])
def test_gmail_cancel_and_error_redirect_safely(client, gmail_config, error):
    query = start_gmail(client)
    response = client.get("/api/oauth/callback/gmail", params={"state":query["state"][0], "error":error}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("cancelled" if error == "access_denied" else "failed")


def test_gmail_provider_error_never_echoes_credentials(client, gmail_config):
    query = start_gmail(client)
    with patch("app.routes.GmailConnector.exchange_code", side_effect=ConnectorError("safe provider failure")):
        response = client.get("/api/oauth/callback/gmail", params={"state":query["state"][0], "code":"sensitive-code"}, follow_redirects=False)
    assert response.headers["location"].endswith("gmail=failed")
    assert "sensitive-code" not in response.text


def test_gmail_reconnect_preserves_refresh_for_same_account_only(client, db_session, gmail_config):
    assert complete_gmail(client)[0].headers["location"].endswith("connected")
    assert complete_gmail(client, refresh=None)[0].headers["location"].endswith("connected")
    query = start_gmail(client)
    with patch("app.routes.GmailConnector.exchange_code", return_value=TokenBundle("other", "other-refresh", None)), patch(
        "app.routes.GmailConnector.mailbox_email", return_value="different@example.com"
    ):
        response = client.get("/api/oauth/callback/gmail", params={"state":query["state"][0], "code":"code"}, follow_redirects=False)
    assert response.headers["location"].endswith("failed")
    assert decrypt_token(db_session.query(Connection).one().encrypted_access_token) == "access"


def test_gmail_missing_offline_token_fails_without_storing_connection(client, db_session, gmail_config):
    assert complete_gmail(client, refresh=None)[0].headers["location"].endswith("failed")
    assert db_session.query(GmailAccount).count() == 0
    assert db_session.query(Connection).count() == 0


def test_gmail_sync_live_idempotent_no_model_and_demo_preserves_connection(client, db_session, gmail_config):
    complete_gmail(client)
    item = NormalizedRawItem("gmail_live:id1", "email_receipt", {"source":"gmail_live", "subject":"Receipt"}, datetime.now(timezone.utc))
    with patch("app.services.GmailConnector.fetch_since", return_value=[item]) as fetch, patch(
        "app.services.run_extraction_for_user"
    ) as extraction:
        first = client.post("/api/sync/gmail")
        second = client.post("/api/sync/gmail")
    assert first.status_code == 200 and first.json()["raw_items_inserted"] == 1
    assert second.json()["raw_items_inserted"] == 0
    assert first.json()["mode"] == "live"
    assert first.json()["pending_items"] == 1
    extraction.assert_not_called()
    fetch.assert_called_with("access", None)
    raw = db_session.query(RawItem).one()
    assert raw.external_id == "gmail_live:mailbox@example.com:id1"
    assert not raw.processed
    assert db_session.query(ModelCall).count() == 0
    client.post("/api/demo/load")
    connection = db_session.query(Connection).filter_by(provider="gmail").one()
    assert connection.status == "connected"
    listed = next(c for c in client.get("/api/connections").json() if c["provider"] == "gmail")
    assert listed["account_email"] == "mailbox@example.com" and listed["pending_items"] == 1


def test_gmail_sync_refreshes_expiring_token(client, db_session, gmail_config):
    complete_gmail(client)
    connection = db_session.query(Connection).one()
    connection.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    refreshed = TokenBundle("new-access", None, datetime.now(timezone.utc) + timedelta(hours=1))
    with patch("app.services.GmailConnector.refresh", return_value=refreshed) as refresh, patch(
        "app.services.GmailConnector.fetch_since", return_value=[]
    ) as fetch:
        assert client.post("/api/sync/gmail").status_code == 200
    refresh.assert_called_once_with("refresh")
    fetch.assert_called_once_with("new-access", None)
    db_session.refresh(connection)
    assert decrypt_token(connection.encrypted_refresh_token) == "refresh"


def test_gmail_sync_recovers_early_expiry_and_surfaces_failures(client, db_session, gmail_config):
    complete_gmail(client)
    refreshed = TokenBundle("new-access", "new-refresh", datetime.now(timezone.utc) + timedelta(hours=1))
    with patch("app.services.GmailConnector.refresh", return_value=refreshed), patch(
        "app.services.GmailConnector.fetch_since", side_effect=[ConnectorError("expired", reconnect=True), []]
    ) as fetch:
        assert client.post("/api/sync/gmail").status_code == 200
        assert fetch.call_count == 2
    with patch("app.services.GmailConnector.fetch_since", side_effect=ConnectorError("Google rate limit reached.")):
        response = client.post("/api/sync/gmail")
    assert response.status_code == 502 and "rate limit" in response.json()["detail"]
    with patch("app.services.GmailConnector.fetch_since", side_effect=ConnectorError("expired", reconnect=True)), patch(
        "app.services.GmailConnector.refresh", side_effect=ConnectorError("Reconnect Gmail.", reconnect=True)
    ):
        assert client.post("/api/sync/gmail").status_code == 409
    assert db_session.query(Connection).one().status == "error"
    assert db_session.query(RawItem).count() == 0


def test_gmail_sync_requires_connection_and_disconnect_retains_data(client, db_session, gmail_config):
    assert client.post("/api/sync/gmail").status_code == 409
    complete_gmail(client)
    db_session.add(RawItem(user_id=db_session.query(Connection).one().user_id, provider="gmail",
                           external_id="kept", item_type="email_receipt", payload_json={}))
    db_session.commit()
    with patch("app.routes.GmailConnector.revoke", side_effect=ConnectorError("Google unreachable")):
        assert client.delete("/api/connections/gmail").status_code == 502
    with patch("app.routes.GmailConnector.revoke") as revoke:
        assert client.delete("/api/connections/gmail").status_code == 200
        revoke.assert_called_once_with("refresh")
    assert db_session.query(GmailAccount).count() == 0
    assert db_session.query(Connection).one().encrypted_refresh_token is None
    assert db_session.query(RawItem).count() == 1
    assert client.post("/api/sync/gmail").status_code == 409


@pytest.mark.parametrize("field,value", [
    ("google_client_id",""), ("token_encryption_key",""), ("token_encryption_key","invalid"),
    ("google_redirect_uri","http://localhost:8000/api/oauth/callback/google"),
])
def test_gmail_connect_rejects_bad_config(client, gmail_config, field, value):
    setattr(gmail_config, field, value)
    assert client.get("/api/connect/gmail").status_code == 503


def test_gmail_wrong_encryption_key_has_safe_reconnect_error(client, db_session, gmail_config):
    complete_gmail(client)
    gmail_config.token_encryption_key = Fernet.generate_key().decode()
    response = client.post("/api/sync/gmail")
    assert response.status_code == 409 and "cannot be decrypted" in response.json()["detail"]
    assert db_session.query(RawItem).count() == 0


def test_gmail_account_deletion_clears_oauth_and_mailbox_records(client, db_session, gmail_config):
    complete_gmail(client)
    start_gmail(client)
    assert client.delete("/api/account/data").status_code == 200
    assert db_session.query(OAuthState).count() == 0
    assert db_session.query(GmailAccount).count() == 0
    assert db_session.query(Connection).count() == 0


def test_process_imported_email_requires_consent(client):
    assert client.post("/api/gmail/process", json={"consent":False}).status_code == 422


def test_process_imported_email_selects_only_one_live_item(client, db_session, user):
    items = [
        RawItem(user_id=user.id, provider="gmail", external_id=external_id,
                item_type="email_receipt", payload_json={"source":source, "body_text":"Ordered ramen."})
        for external_id, source in [("sample-one","sample"), ("gmail_live:a:1","gmail_live"), ("gmail_live:a:2","gmail_live")]
    ]
    db_session.add_all(items)
    db_session.commit()
    with patch("app.services.extract_one_imported_email", return_value=[
        {"category":"food", "statement":"Ordered ramen", "evidence_quote":"Ordered ramen"}
    ]) as extraction:
        response = client.post("/api/gmail/process", json={"consent":True})
    assert response.status_code == 200
    assert response.json()["emails_processed"] == 1
    assert response.json()["traits_created"] == 1
    assert extraction.call_count == 1
    trait = db_session.query(Trait).one()
    assert trait.privacy_tier == "private" and trait.confidence <= 0.5
    assert trait.user_overridden
    fact = db_session.query(Fact).one()
    assert fact.privacy_tier == "private" and fact.synthesized
    assert fact.id in trait.supporting_fact_ids
    assert db_session.query(RawItem).filter_by(processed=True).count() == 1
    assert not db_session.query(RawItem).filter_by(external_id="sample-one").one().processed


def test_process_email_empty_and_failure_are_retry_safe(client, db_session, user):
    from app.pipeline import EmailProcessingError
    with patch("app.services.extract_one_imported_email") as extraction:
        assert client.post("/api/gmail/process", json={"consent":True}).json()["emails_processed"] == 0
        extraction.assert_not_called()


    item = RawItem(user_id=user.id, provider="gmail", external_id="gmail_live:a:1",
                   item_type="email_receipt", payload_json={"source":"gmail_live", "body_text":"Booking"})
    db_session.add(item)
    db_session.commit()
    with patch("app.services.extract_one_imported_email", side_effect=EmailProcessingError("Invalid model response; no automatic retry.")):
        response = client.post("/api/gmail/process", json={"consent":True}, headers={"Origin":"http://localhost:5173"})
    assert response.status_code == 502
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    db_session.refresh(item)
    assert not item.processed and db_session.query(Fact).count() == 0
    with patch("app.services.extract_one_imported_email", return_value=[]):
        response = client.post("/api/gmail/process", json={"consent":True})
    assert response.status_code == 200 and response.json()["facts_created"] == 0
    with patch("app.services.extract_one_imported_email") as extraction:
        assert client.post("/api/gmail/process", json={"consent":True}).json()["emails_processed"] == 0
        extraction.assert_not_called()


def test_email_processing_lease_excludes_concurrent_request(client, db_session, user):
    from app.db import EmailProcessingLease
    from datetime import datetime, timedelta, timezone
    db_session.add(EmailProcessingLease(user_id=user.id, owner="other", expires_at=datetime.now(timezone.utc)+timedelta(minutes=1)))
    db_session.commit()
    with patch("app.services.extract_one_imported_email") as extraction:
        assert client.post("/api/gmail/process", json={"consent":True}).status_code == 409
        extraction.assert_not_called()
    lease = db_session.get(EmailProcessingLease, user.id)
    assert lease.owner == "other"
    lease.expires_at = datetime.now(timezone.utc)-timedelta(minutes=1)
    db_session.commit()
    assert client.post("/api/gmail/process", json={"consent":True}).status_code == 200
    assert db_session.get(EmailProcessingLease, user.id) is None


def test_process_email_preserves_overrides_and_ignores_other_sources(client, db_session, user):
    from app.db import BlockedTrait, User
    other = User(email="other@example.invalid")
    db_session.add(other)
    db_session.flush()
    db_session.add_all([
        RawItem(user_id=other.id, provider="gmail", external_id="gmail_live:other:1",
                item_type="email_receipt", payload_json={"source":"gmail_live"}),
        RawItem(user_id=user.id, provider="spotify", external_id="gmail_live:wrong:1",
                item_type="track", payload_json={"source":"gmail_live"}),
        Trait(user_id=user.id, category="food", key="existing", value="User value",
              privacy_tier="ask", user_overridden=True),
    ])
    item = RawItem(user_id=user.id, provider="gmail", external_id="gmail_live:a:1",
                   item_type="email_receipt", payload_json={"source":"gmail_live"})
    db_session.add(item)
    db_session.flush()
    db_session.add(BlockedTrait(user_id=user.id, category="food", key=f"email_observation_{item.id}_0"))
    db_session.commit()
    with patch("app.services.extract_one_imported_email", return_value=[
        {"category":"food", "statement":"Blocked", "evidence_quote":"Blocked"},
        {"category":"food", "statement":"Allowed", "evidence_quote":"Allowed"},
    ]) as extraction:
        response = client.post("/api/gmail/process", json={"consent":True})
    assert response.status_code == 200 and response.json()["traits_created"] == 1
    assert extraction.call_args.args[2].id == item.id
    assert db_session.query(Trait).filter_by(key="existing").one().value == "User value"
    assert db_session.query(Trait).filter_by(key=f"email_observation_{item.id}_1").one().value == "Allowed"
    with patch("app.services.extract_one_imported_email") as extraction:
        assert client.post("/api/gmail/process", json={"consent":True}).json()["emails_processed"] == 0
        extraction.assert_not_called()


def test_process_email_rejects_bad_source_without_call(client, db_session, user):
    db_session.add(RawItem(user_id=user.id, provider="gmail", external_id="gmail_live:a:1",
                           item_type="email_receipt", payload_json={"source":"sample"}))
    db_session.commit()
    with patch("app.services.extract_one_imported_email") as extraction:
        assert client.post("/api/gmail/process", json={"consent":True}).status_code == 502
        extraction.assert_not_called()


def test_batch_route_requires_consent_and_no_items_is_free(client):
    with patch("app.services.classify_imported_emails") as call:
        assert client.post("/api/gmail/process-batch", json={}).status_code == 422
        assert client.post("/api/gmail/process-batch", json={"consent":False}).status_code == 422
        assert client.post("/api/gmail/process-batch", json={"consent":True}).json()["results"] == []
        call.assert_not_called()


def test_today_end_to_end_flags_imported_email_and_done_is_free(client, db_session, user, today_email, today_candidate):
    import json
    from app.db import TodayItem, TodayScan
    item = today_email()
    with patch("app.pipeline._email_model_response", return_value=json.dumps({"items":[today_candidate()]})) as model, patch(
        "app.services._today_now", return_value=datetime(2026, 10, 30, 6, tzinfo=timezone.utc),
    ):
        assert client.get("/api/today?timezone_name=Asia%2FKolkata").json()["pending_emails"] == 1
        model.assert_not_called()
        assert client.post("/api/today/process", json={"consent":False,"timezone_name":"UTC"}).status_code == 422
        assert client.post("/api/today/process", json={"consent":True,"timezone_name":"Asia/Kolkata"}).status_code == 200
        view = client.get("/api/today?timezone_name=Asia%2FKolkata").json()
        assert view["action_count"] == 1 and view["reminder_count"] == 0
        action = view["items"][0]
        assert action["source_url"].startswith("https://mail.google.com/")
        assert client.patch("/api/today/" + action["id"], json={"action":"done"}).status_code == 200
        assert client.get("/api/today?timezone_name=Asia%2FKolkata").json()["action_count"] == 0
        assert client.post("/api/today/process", json={"consent":True,"timezone_name":"UTC"}).json()["emails_checked"] == 0
        model.assert_called_once()
    assert item.processed and db_session.query(TodayScan).one().status == "extracted"
    assert db_session.query(TodayItem).one().status == "done"
    assert db_session.query(Trait).count() == db_session.query(Fact).count() == 0
    assert client.delete("/api/account/data").status_code == 200
    assert db_session.query(TodayItem).count() == db_session.query(TodayScan).count() == 0


def test_today_eod_request_appears_and_recheck_is_free(client, db_session, user, gmail_config, today_deadline):
    import base64
    import httpx
    import json
    from app.db import TodayScan
    body, candidate = today_deadline
    complete_gmail(client)
    received = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)
    connection = db_session.query(Connection).filter_by(provider="gmail").one()
    connection.last_synced_at = received + timedelta(hours=1)
    db_session.commit()
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages":[{"id":"diagram-request"}]})
        return httpx.Response(200, json={
            "id":"diagram-request","internalDate":str(int(received.timestamp() * 1000)),
            "payload":{"mimeType":"text/plain","headers":[{"name":"Subject","value":"Document deadline"}],
                       "body":{"data":base64.urlsafe_b64encode(body.encode()).decode()}},
        })
    with patch("app.pipeline._email_model_response", return_value=json.dumps({"items":[candidate]})) as model, patch(
        "app.services._today_now", return_value=datetime(2026, 10, 30, 8, tzinfo=timezone.utc),
    ):
        with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))), patch(
            "app.connectors.datetime", wraps=datetime,
        ) as clock:
            clock.now.return_value = received + timedelta(hours=2)
            synced = client.post("/api/sync/gmail")
        assert synced.status_code == 200 and synced.json()["raw_items_inserted"] == 1
        query = calls[0].url.params["q"]
        assert "EOD" in query and f"after:{int((received + timedelta(hours=2) - timedelta(days=30)).timestamp())}" in query
        assert db_session.query(RawItem).one().payload_json["body_text"] == body
        model.assert_not_called()
        request = {"consent":True,"timezone_name":"Asia/Calcutta"}
        assert client.post("/api/today/process", json=request).json()["results"][0]["status"] == "checked"
        view = client.get("/api/today?timezone_name=Asia%2FCalcutta").json()
        assert view["action_count"] == 1 and view["reminder_count"] == 0
        assert view["items"][0]["title"] == "Send architectural diagram" and view["items"][0]["at"] is None
        scan = db_session.query(TodayScan).one()
        scan.status = "failed"
        db_session.commit()
        assert client.post("/api/today/process", json=request).json()["results"][0]["status"] == "checked"
        assert client.get("/api/today?timezone_name=Asia%2FCalcutta").json()["action_count"] == 1
        model.assert_called_once()


def test_today_scope_controls_timezone_and_ownership(client, db_session, user, today_email, today_candidate):
    from app.db import TodayItem, EmailProcessingLease
    other = User(email="today-other@example.invalid")
    db_session.add(other)
    db_session.flush()
    row = TodayItem(user_id=other.id, event_key="other", kind="action", title="Review another proposal",
                    detail="Private to another user.", day="2026-10-30", source_item_ids=[])
    db_session.add(row)
    db_session.commit()
    with patch("app.pipeline._client", side_effect=AssertionError("No requests")):
        assert client.get("/api/today?timezone_name=Invalid%2FZone").status_code == 422
        assert client.get("/api/today?timezone_name=" + "X" * 100).status_code == 422
        assert client.post("/api/today/process", json={"consent":True,"timezone_name":"../invalid"}).status_code == 422
        assert client.get("/api/today").json()["items"] == []
        assert client.patch("/api/today/" + row.id, json={"action":"done"}).status_code == 404
        assert client.patch("/api/today/missing", json={"action":"invalid"}).status_code == 422
        today_email(external_id="sample-only")
        assert client.post("/api/today/process", json={"consent":True,"timezone_name":"UTC"}).json()["emails_checked"] == 0
        db_session.add(EmailProcessingLease(user_id=user.id, owner="other",
                                           expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        db_session.commit()
        assert client.post("/api/today/process", json={"consent":True,"timezone_name":"UTC"}).status_code == 409


def test_today_snooze_and_dismiss_routes(client, db_session, user, today_email, today_candidate):
    from app.db import TodayItem, EmailProcessingLease
    today_email()
    with patch("app.services.extract_today_candidates", return_value=[today_candidate()]), patch(
        "app.services._today_now", return_value=datetime(2026, 10, 30, 6, tzinfo=timezone.utc),
    ), patch("app.routes._today_now", return_value=datetime(2026, 10, 30, 6, tzinfo=timezone.utc)):
        assert client.post("/api/today/process", json={"consent":True,"timezone_name":"UTC"}).status_code == 200
        row = db_session.query(TodayItem).one()
        assert client.patch("/api/today/" + row.id, json={"action":"snooze"}).status_code == 200
        assert client.get("/api/today").json()["action_count"] == 0
        assert client.patch("/api/today/" + row.id, json={"action":"dismiss"}).status_code == 200
        assert row.status == "dismissed"
        assert client.patch("/api/today/" + row.id, json={"action":"snooze"}).status_code == 409
        db_session.add(EmailProcessingLease(user_id=user.id, owner="other",
                                           expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        db_session.commit()
        assert client.patch("/api/today/" + row.id, json={"action":"done"}).status_code == 409


def test_refresh_preferences_upgrades_only_unreviewed_aggregates_without_model_calls(client, db_session, user):
    from tests.test_services import _record_activity, _food_event
    _record_activity(db_session, user, _food_event("Chicken Dum Biryani"))
    db_session.add_all([
        Trait(user_id=user.id, category="food", key="activity_foods", value="Old leaderboard", privacy_tier="private"),
        Trait(user_id=user.id, category="food", key="sample_preference", value="Japanese food", privacy_tier="share"),
    ])
    db_session.commit()
    with patch("app.pipeline._client", side_effect=AssertionError("No paid calls")):
        first = client.post("/api/profile/activity/refresh")
        ids = {trait.id for trait in db_session.query(Trait)}
        assert client.post("/api/profile/activity/refresh").status_code == 200
    assert first.status_code == 200 and "No model calls" in first.json()["message"]
    assert ids == {trait.id for trait in db_session.query(Trait)}
    assert db_session.query(Trait).filter_by(key="activity_foods").count() == 0
    assert db_session.query(Trait).filter_by(key="sample_preference").one().value == "Japanese food"
    assert len(client.get("/api/profile").json()["food"]) == 3


def test_refresh_preferences_waits_for_processing_lease(client, db_session, user):
    from app.db import EmailProcessingLease
    db_session.add(EmailProcessingLease(user_id=user.id, owner="other",
                                       expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
    db_session.commit()
    assert client.post("/api/profile/activity/refresh").status_code == 409


def _process_food_batch(client, db, user, count=2):
    from tests.test_services import _live_emails, _food_event, _classify_food
    _live_emails(db, user, count)
    with patch("app.services.classify_imported_emails", side_effect=_classify_food), patch(
        "app.services.extract_email_event", return_value=_food_event(),
    ):
        response = client.post("/api/gmail/process-batch", json={"consent":True})
    assert response.status_code == 200
    return response.json()


def test_activity_routes_rank_remove_recalculate_and_preserve_raw(client, db_session, user):
    _process_food_batch(client, db_session, user)
    activity = client.get("/api/profile/activity").json()
    assert activity["rankings"]["foods"][0]["count"] == 2
    assert len(activity["events"]) == 2
    fact_id = activity["events"][0]["fact_id"]
    assert client.delete("/api/profile/activity/" + fact_id).status_code == 200
    assert client.get("/api/profile/activity").json()["rankings"]["foods"][0]["count"] == 1
    assert db_session.query(RawItem).count() == 2
    other_id = client.get("/api/profile/activity").json()["events"][0]["fact_id"]
    assert client.delete("/api/profile/activity/" + other_id).status_code == 200
    assert client.get("/api/profile/activity").json()["rankings"] == {}
    assert db_session.query(Trait).count() == 0
    assert client.delete("/api/profile/activity/" + other_id).status_code == 404


def test_review_unknown_can_requeue_without_paid_calls(client, db_session, user):
    from app.db import EmailIngestion
    from tests.test_services import _live_emails
    items = _live_emails(db_session, user, 1)
    with patch("app.services.classify_imported_emails", return_value={items[0].id:"unknown"}), patch(
        "app.services.extract_email_event",
    ) as extraction:
        assert client.post("/api/gmail/process-batch", json={"consent":True}).status_code == 200
        extraction.assert_not_called()
    assert client.get("/api/profile/activity").json()["review"][0]["status"] == "review"
    with patch("app.services.classify_imported_emails") as classify:
        assert client.post(f"/api/profile/activity/{items[0].id}/retry").status_code == 200
        classify.assert_not_called()
    assert db_session.get(EmailIngestion, items[0].id) is None
    db_session.refresh(items[0])
    assert not items[0].processed
    assert client.post(f"/api/profile/activity/{items[0].id}/retry").status_code == 404


def test_batch_and_activity_account_isolation(client, db_session, user):
    from tests.test_services import _live_emails
    other = User(email="different@example.invalid")
    db_session.add(other)
    db_session.commit()
    _live_emails(db_session, other, 1)
    db_session.add(RawItem(user_id=user.id, provider="gmail", external_id="sample-only",
                           item_type="email_receipt", payload_json={"source":"sample"}))
    db_session.commit()
    with patch("app.services.classify_imported_emails") as classify:
        assert client.post("/api/gmail/process-batch", json={"consent":True}).json()["results"] == []
        classify.assert_not_called()
    assert client.get("/api/profile/activity").json()["events"] == []
    assert client.delete("/api/profile/activity/not-owned").status_code == 404


def test_activity_account_delete_clears_state(client, db_session, user):
    from app.db import EmailIngestion
    _process_food_batch(client, db_session, user, count=1)
    assert db_session.query(EmailIngestion).count() == 1
    assert client.delete("/api/account/data").status_code == 200
    assert db_session.query(EmailIngestion).count() == 0


def test_batch_http_to_mocked_nano_and_super_roundtrip(client, db_session, user, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app import pipeline
    from tests.test_services import _live_emails, _food_event
    items = _live_emails(db_session, user, 5)
    for item in items:
        item.payload_json = {"source":"gmail_live", "body_text":"Dosa from Cafe A. Discount 70.80 coupon MISSEDYOU"}
    db_session.commit()
    monkeypatch.setattr(pipeline, "get_settings", lambda: SimpleNamespace(
        nebius_api_key="test", nemotron_nano_model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        nemotron_super_model="nvidia/nemotron-3-super-120b-a12b",
        daily_spend_cap_usd=5, gmail_nano_debug_output=False, app_env="development",
    ))
    def reply(**kwargs):
        source = json.loads(kwargs["messages"][1]["content"])
        data = {"emails":[{"email_id": item["email_id"], "type":"food_order"} for item in source]} if isinstance(source, list) else _food_event()
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=60),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(data)))],
        )
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.side_effect = reply
    with patch.object(pipeline, "_client", return_value=fake):
        response = client.post("/api/gmail/process-batch", json={"consent":True})
    assert response.status_code == 200 and response.json()["emails_processed"] == 5
    assert call.call_count == 6
    models = [entry.kwargs["model"] for entry in call.call_args_list]
    assert models == ["nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"] + ["nvidia/nemotron-3-super-120b-a12b"] * 5
    assert all(entry.kwargs["response_format"]["type"] == "json_schema" for entry in call.call_args_list)
    assert db_session.query(ModelCall).count() == 6
    assert {row.task for row in db_session.query(ModelCall)} == {"gmail_classification","gmail_food_order"}
    assert db_session.query(ModelCall).filter_by(model="super").count() == 5
    assert all(fact.model_used == "super" for fact in db_session.query(Fact))
    traits = db_session.query(Trait).all()
    assert len(traits) == 2 and all(trait.privacy_tier == "private" for trait in traits)
    assert all("MISSEDYOU" not in trait.value and "70.80" not in trait.value for trait in traits)
    assert client.get("/api/profile/activity").json()["rankings"]["foods"][0]["count"] == 5


def test_retry_cached_classification_calls_only_super(client, db_session, user, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app import pipeline
    from app.db import EmailIngestion
    from tests.test_services import _live_emails, _food_event
    item = _live_emails(db_session, user, 1)[0]
    item.payload_json = {"source":"gmail_live","body_text":"Dosa from Cafe A. Order ID: TEST123"}
    db_session.add(EmailIngestion(raw_item_id=item.id, user_id=user.id, kind="food_order", status="failed"))
    db_session.commit()
    monkeypatch.setattr(pipeline, "get_settings", lambda: SimpleNamespace(
        nebius_api_key="test", nemotron_nano_model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        nemotron_super_model="nvidia/nemotron-3-super-120b-a12b", daily_spend_cap_usd=5,
        gmail_nano_debug_output=False, app_env="development",
    ))
    event = _food_event()
    event["order_id"] = {"value":"TEST123","quote":"Order ID: TEST123"}
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.return_value = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100,completion_tokens=60),
        choices=[SimpleNamespace(finish_reason="stop",message=SimpleNamespace(content=json.dumps(event)))])
    with patch.object(pipeline, "_client", return_value=fake):
        response = client.post("/api/gmail/process-batch", json={"consent":True})
    assert response.status_code == 200 and response.json()["results"][0]["status"] == "extracted"
    call.assert_called_once()
    assert call.call_args.kwargs["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert db_session.query(ModelCall).one().model == "super"
    assert db_session.query(Fact).one().model_used == "super"


def test_evidence_failure_keeps_candidate_and_retry_is_local(client, db_session, user):
    import json
    from app import pipeline
    from app.db import EmailIngestion
    from tests.test_services import _live_emails
    item = _live_emails(db_session, user, 1)[0]
    item.payload_json = {"source":"gmail_live","body_text":"Badminton at Arena. Booking ID: TEST123"}
    db_session.add(EmailIngestion(raw_item_id=item.id, user_id=user.id, kind="sports_booking", status="classified"))
    db_session.commit()
    event = {"sport":{"value":"Badminton","quote":"Badminton"},"venue":{"value":"Arena","quote":"Arena"},
             "order_id":{"value":"TEST123","quote":"Booking ID: TEST123"}}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)) as model, patch.object(
        pipeline, "validate_email_event_evidence", side_effect=pipeline.EmailProcessingError("Synthetic evidence rejection"),
    ):
        response = client.post("/api/gmail/process-batch", json={"consent":True})
    assert response.json()["results"][0]["status"] == "failed"
    model.assert_called_once()
    state = db_session.get(EmailIngestion, item.id)
    assert state.event["candidate"] == event and state.status == "failed"
    with patch.object(pipeline, "_email_model_response") as model:
        response = client.post("/api/gmail/process-batch", json={"consent":True})
        model.assert_not_called()
    assert response.json()["results"][0]["status"] == "extracted"
    assert db_session.query(Fact).count() == 1
    assert db_session.query(RawItem).filter_by(processed=False).count() == 0


def test_removed_order_stays_excluded_and_user_edits_are_preserved(client, db_session, user):
    from tests.test_services import _live_emails, _food_event, _classify_food
    _live_emails(db_session, user, 1)
    with patch("app.services.classify_imported_emails", side_effect=_classify_food), patch(
        "app.services.extract_email_event", return_value=_food_event(order_id="order-A"),
    ):
        client.post("/api/gmail/process-batch", json={"consent":True})
    trait = db_session.query(Trait).filter_by(key="preference_food_south_indian").one()
    client.patch(f"/api/traits/{trait.id}", json={"value":"My food notes", "privacy_tier":"ask"})
    fact_id = client.get("/api/profile/activity").json()["events"][0]["fact_id"]
    assert client.delete(f"/api/profile/activity/{fact_id}").status_code == 200
    db_session.refresh(trait)
    assert trait.value == "My food notes" and trait.privacy_tier == "private"
    assert trait.evidence_count == 0 and "Evidence removed" in trait.summary
    # New message ID, same explicitly identified order.
    db_session.add(RawItem(user_id=user.id, provider="gmail", external_id="gmail_live:mailbox:later-receipt",
                           item_type="email_receipt", payload_json={"source":"gmail_live"}))
    db_session.commit()
    with patch("app.services.classify_imported_emails", side_effect=_classify_food), patch(
        "app.services.extract_email_event", return_value=_food_event(order_id="order-A"),
    ):
        result = client.post("/api/gmail/process-batch", json={"consent":True})
    assert result.json()["results"][0]["status"] == "excluded"
    assert client.get("/api/profile/activity").json()["rankings"] == {}
    assert db_session.query(Fact).count() == 0


def test_batch_and_review_reject_concurrent_operations(client, db_session, user):
    from app.db import EmailIngestion, EmailProcessingLease
    from datetime import datetime, timedelta, timezone
    from tests.test_services import _live_emails
    items = _live_emails(db_session, user, 1)
    db_session.add_all([
        EmailIngestion(raw_item_id=items[0].id, user_id=user.id, kind="unknown", status="review"),
        EmailProcessingLease(user_id=user.id, owner="other", expires_at=datetime.now(timezone.utc)+timedelta(minutes=1)),
    ])
    db_session.commit()
    assert client.post("/api/gmail/process-batch", json={"consent":True}).status_code == 409
    assert client.post(f"/api/profile/activity/{items[0].id}/retry").status_code == 409


def test_classification_error_returns_cors_and_batch_preserves_pending(client, db_session, user):
    from app.pipeline import EmailProcessingError
    from tests.test_services import _live_emails
    _live_emails(db_session, user, 1)
    with patch("app.services.classify_imported_emails", side_effect=EmailProcessingError("Synthetic classification failure")):
        response = client.post("/api/gmail/process-batch", json={"consent":True}, headers={"Origin":"http://localhost:5173"})
    assert response.status_code == 502 and response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert db_session.query(RawItem).filter_by(processed=False).count() == 1

# ---- profile ----

def life_context():
    return {
        "home_city": "Bengaluru",
        "home_area": "Indiranagar",
        "office_city": "Bengaluru",
        "office_area": "Whitefield",
        "work_arrangement": "Hybrid, two office days",
        "household_members": [
            {"name": "Partner", "relationship": "Spouse", "likes": "Italian food",
             "dislikes": "Loud venues", "dietary_needs": "Vegetarian"}
        ],
    }


def test_my_life_round_trip_update_and_clear_without_llm(client):
    with patch("app.routes.call_llm") as llm:
        empty = client.get("/api/profile/my-life").json()
        assert empty["home_city"] == ""
        assert empty["household_members"] == []
        data = life_context()
        assert client.put("/api/profile/my-life", json=data).json() == data
        assert client.get("/api/profile/my-life").json() == data
        data["home_area"] = "Koramangala"
        data["household_members"] = []
        assert client.put("/api/profile/my-life", json=data).json() == data
        assert client.put("/api/profile/my-life", json=empty).json() == empty
        assert client.get("/api/profile/my-life").json() == empty
        llm.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"home_city": "x" * 121},
    {"office_area": "x" * 121},
    {"work_arrangement": "x" * 301},
    {"home_city": None},
    {"household_members": [{"name": "", "relationship": "Sibling"}]},
    {"household_members": [{"name": "Sam", "relationship": " "}]},
    {"household_members": [{"name": "Sam", "relationship": "Sibling", "likes": "x" * 301}]},
    {"household_members": [{"name": "Sam", "relationship": "Sibling"}] * 11},
    {"unexpected_field": "not accepted"},
])
def test_my_life_rejects_invalid_data_without_overwriting(client, changes):
    data = life_context()
    client.put("/api/profile/my-life", json=data)
    assert client.put("/api/profile/my-life", json={**data, **changes}).status_code == 422
    assert client.get("/api/profile/my-life").json() == data


def test_my_life_trims_fields(client):
    response = client.put("/api/profile/my-life", json={"home_city": " Bengaluru "})
    assert response.status_code == 200
    assert response.json()["home_city"] == "Bengaluru"


def test_my_life_is_scoped_to_user_and_deleted_with_account(client, db_session, user):
    from app.main import app
    from app.routes import get_current_user

    data = life_context()
    client.put("/api/profile/my-life", json=data)
    other = User(email="other@example.com")
    db_session.add(other)
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.get("/api/profile/my-life").json()["home_city"] == ""
    client.put("/api/profile/my-life", json={"home_city": "Chennai"})
    app.dependency_overrides[get_current_user] = lambda: user
    client.delete("/api/account/data")
    assert client.get("/api/profile/my-life").json()["home_city"] == ""
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.get("/api/profile/my-life").json()["home_city"] == "Chennai"


def test_my_life_context_only_in_personalized_chat_and_compare(client):
    client.put("/api/profile/my-life", json=life_context())
    with patch("app.routes.call_llm", side_effect=["generic", "personal"]) as llm:
        assert client.post("/api/chat/compare", json={"message": "Dinner near home?"}).status_code == 200
    baseline, personal = [call.kwargs["system_prompt"] for call in llm.call_args_list]
    assert "Indiranagar" not in baseline and "Spouse" not in baseline
    assert "Indiranagar" in personal and "Whitefield" in personal and "Italian food" in personal
    assert "travel" in personal and "household" in personal
    with patch("app.routes.call_llm_stream", return_value=iter(["Hi"])) as stream:
        client.post("/api/chat", json={"message": "Dinner?", "mode": "personalized"})
    assert "Indiranagar" in stream.call_args.kwargs["system_prompt"]
    with patch("app.routes.call_llm_stream", return_value=iter(["Hi"])) as stream:
        client.post("/api/chat", json={"message": "Dinner?", "mode": "baseline"})
    assert "Indiranagar" not in stream.call_args.kwargs["system_prompt"]


def test_my_life_not_exported_as_mcp_traits(client, db_session, user):
    from app import mcp

    client.put("/api/profile/my-life", json=life_context())
    token = mcp.current_mcp_user_id.set(user.id)
    try:
        with patch("app.mcp.SessionLocal", return_value=db_session):
            outputs = [
                mcp.get_profile_summary(),
                mcp.get_preferences("my_life"),
                mcp.search_memory("where does my family live"),
            ]
        assert all("Indiranagar" not in output and "Italian food" not in output for output in outputs)
    finally:
        mcp.current_mcp_user_id.reset(token)


def test_missing_my_life_location_asks_rather_than_assumes(client):
    with patch("app.routes.call_llm", side_effect=["generic", "personal"]) as llm:
        client.post("/api/chat/compare", json={"message": "Dinner nearby?"})
    prompt = llm.call_args_list[1].kwargs["system_prompt"]
    assert "ask" in prompt.lower()
    assert '"home_city":""' in prompt


def test_my_life_survives_demo_load_and_rebuild(client):
    data = life_context()
    client.put("/api/profile/my-life", json=data)
    with patch("app.routes.call_llm") as llm:
        assert client.post("/api/demo/load").status_code == 200
        llm.assert_not_called()
    with patch("app.routes.run_privacy_tagging_for_user", return_value=0), patch(
        "app.routes.run_synthesis_for_user", return_value=0
    ):
        assert client.post("/api/profile/rebuild").status_code == 200
    assert client.get("/api/profile/my-life").json() == data


def test_my_life_updates_and_removals_apply_to_next_answer(client):
    data = life_context()
    for next_data, present, absent in [
        (data, "Indiranagar", "Koramangala"),
        ({**data, "home_area": "Koramangala"}, "Koramangala", "Indiranagar"),
        ({}, '"home_city":""', "Koramangala"),
    ]:
        client.put("/api/profile/my-life", json=next_data)
        with patch("app.routes.call_llm", side_effect=["generic", "personal"]) as llm:
            client.post("/api/chat/compare", json={"message": "Dinner nearby?"})
        prompt = llm.call_args_list[1].kwargs["system_prompt"]
        assert present in prompt and absent not in prompt


def test_get_profile_groups_traits_by_category(client, db_session, user):
    db_session.add(Trait(user_id=user.id, category="food", key="k1", value="Loves ramen", confidence=0.9, privacy_tier="share"))
    db_session.commit()
    response = client.get("/api/profile")
    assert response.status_code == 200
    assert "food" in response.json()
    assert response.json()["food"][0]["value"] == "Loves ramen"


def test_patch_trait_sets_user_overridden(client, db_session, user):
    trait = Trait(user_id=user.id, category="food", key="k1", value="Loves ramen", confidence=0.9, privacy_tier="share")
    db_session.add(trait)
    db_session.commit()
    db_session.refresh(trait)

    response = client.patch(f"/api/traits/{trait.id}", json={"privacy_tier": "private"})
    assert response.status_code == 200
    assert response.json()["privacy_tier"] == "private"
    assert response.json()["user_overridden"] is True


def test_delete_trait_blocks_recreation(client, db_session, user):
    trait = Trait(user_id=user.id, category="food", key="k1", value="Loves ramen", confidence=0.9, privacy_tier="share")
    db_session.add(trait)
    db_session.commit()
    db_session.refresh(trait)

    response = client.delete(f"/api/traits/{trait.id}")
    assert response.status_code == 200
    assert db_session.query(Trait).count() == 0


def test_rebuild_profile_runs_tagging_and_synthesis(client):
    with patch("app.routes.run_privacy_tagging_for_user", return_value=1) as mocked_tag, patch(
        "app.routes.run_synthesis_for_user", return_value=1
    ) as mocked_synth:
        response = client.post("/api/profile/rebuild")
    assert response.status_code == 200
    mocked_tag.assert_called_once()
    mocked_synth.assert_called_once()


@pytest.mark.parametrize("seconds,status", [(5, 429), (60, 200)])
def test_rebuild_cooldown_handles_sqlite_naive_timestamps(client, db_session, user, seconds, status):
    db_session.add(ModelCall(
        user_id=user.id, task="synthesis", model="ultra", model_id="test",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=seconds),
    ))
    db_session.commit()
    with patch("app.routes.run_privacy_tagging_for_user", return_value=0) as tag, patch(
        "app.routes.run_synthesis_for_user", return_value=0
    ):
        response = client.post("/api/profile/rebuild", headers={"Origin":"http://localhost:5173"})
    assert response.status_code == status
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert tag.call_count == (1 if status == 200 else 0)


# ---- chat ----


def test_web_compare_searches_once_and_shares_sources_not_profile(client,tavily_http):
    import json
    client.put("/api/profile/my-life",json={"home_city":"PRIVATE_HOME_LOCATION"})
    requests = tavily_http({"results":[{"title":"Public guide","url":"https://example.org/guide","content":"Untrusted public details."}]})
    with patch("app.routes.call_llm",side_effect=["baseline","personalized"]) as model:
        response = client.post("/api/chat/compare",json={"message":"PRIVATE_CHAT_CONTENT","web_query":"public sports guide"})
    assert response.status_code == 200 and len(requests) == 1
    assert json.loads(requests[0].content)["query"] == "public sports guide"
    assert "PRIVATE_" not in requests[0].content.decode()
    assert response.json()["web_search_used"] is True
    assert response.json()["sources"] == [{"title":"Public guide","url":"https://example.org/guide"}]
    prompts = [call.kwargs["system_prompt"] for call in model.call_args_list]
    assert all("Untrusted public details." in prompt and "https://example.org/guide" in prompt for prompt in prompts)
    assert "PRIVATE_HOME_LOCATION" not in prompts[0] and "PRIVATE_HOME_LOCATION" in prompts[1]


def test_web_chat_exposes_sources_to_browser_without_changing_stream(client,tavily_http):
    import json
    requests = tavily_http({"results":[{"title":"Public guide","url":"https://example.org/guide","content":"Public details."}]})
    with patch("app.routes.call_llm_stream",return_value=iter(["Web ","answer"])) as model:
        response = client.post("/api/chat",json={"message":"Answer my question","web_query":"public guide"},
                               headers={"Origin":"http://localhost:5173"})
    assert response.status_code == 200 and response.text == "Web answer"
    assert response.headers["x-web-search-used"] == "true"
    assert json.loads(response.headers["x-web-sources"]) == [{"title":"Public guide","url":"https://example.org/guide"}]
    assert "X-Web-Sources" in response.headers["access-control-expose-headers"]
    assert len(requests) == 1 and "Public details." in model.call_args.kwargs["system_prompt"]


@pytest.mark.parametrize("endpoint",["/api/chat","/api/chat/compare"])
def test_chat_without_web_query_never_searches(client,endpoint):
    with patch("app.routes.search_public_web") as search, patch(
        "app.routes.call_llm_stream",return_value=iter(["answer"]),
    ), patch("app.routes.call_llm",return_value="answer"):
        response = client.post(endpoint,json={"message":"Use only my saved preferences"})
    assert response.status_code == 200
    search.assert_not_called()
    if endpoint.endswith("compare"):
        assert response.json()["web_search_used"] is False and response.json()["sources"] == []
    else:
        assert response.headers["x-web-search-used"] == "false"


@pytest.mark.parametrize("query",["","   ","x"*301,False,["private"]])
def test_chat_rejects_invalid_public_query_without_search_or_model(client,query):
    with patch("app.routes.search_public_web") as search, patch("app.routes.call_llm_stream") as model:
        response = client.post("/api/chat",json={"message":"Question","web_query":query})
    assert response.status_code == 422
    search.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize("endpoint",["/api/chat","/api/chat/compare"])
def test_tavily_failure_blocks_models_and_does_not_save_partial_chat(client,db_session,tavily_http,endpoint):
    from app.db import ChatMessage
    requests = tavily_http({"detail":"PRIVATE_PROVIDER_BODY"},status=429)
    with patch("app.routes.call_llm_stream") as stream, patch("app.routes.call_llm") as model:
        response = client.post(endpoint,json={"message":"Question","web_query":"public topic"})
    assert response.status_code == 502 and "credit limit" in response.json()["detail"]
    assert "PRIVATE_PROVIDER_BODY" not in response.text and len(requests) == 1
    stream.assert_not_called()
    model.assert_not_called()
    assert db_session.query(ChatMessage).count() == 0


def test_missing_tavily_key_is_explicit_without_model_or_network(client,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("app.services.get_settings",lambda:SimpleNamespace(tavily_api_key=""))
    with patch("app.services.httpx.Client") as http, patch("app.routes.call_llm") as model:
        response = client.post("/api/chat/compare",json={"message":"Question","web_query":"public topic"})
    assert response.status_code == 503 and "TAVIL_API_KEY" in response.json()["detail"]
    model.assert_not_called()
    http.assert_not_called()


def test_no_web_results_are_explicit_and_unicode_metadata_is_safe(client,tavily_http):
    import json
    tavily_http({"results":[]})
    with patch("app.routes.call_llm",return_value="No sources found.") as model:
        response = client.post("/api/chat/compare",json={"message":"Question","web_query":"public topic"})
    assert response.status_code == 200 and response.json()["sources"] == [] and response.json()["web_search_used"]
    assert "search found no sources" in model.call_args.kwargs["system_prompt"]
    tavily_http({"results":[{"title":"Public \u00e9 guide","url":"https://example.org/\u00e9","content":"Public facts."}]})
    with patch("app.routes.call_llm_stream",return_value=iter(["answer"])):
        response = client.post("/api/chat",json={"message":"Question","web_query":"public topic"})
    header = response.headers["x-web-sources"]
    assert header.isascii() and len(header) < 8192
    assert json.loads(header)[0]["title"] == "Public \u00e9 guide"
    assert json.loads(header)[0]["url"] == "https://example.org/%C3%A9"


def test_chat_personalized_streams_reply_and_logs_used_traits(client, db_session, user):
    db_session.add(Trait(user_id=user.id, category="food", key="k1", value="Loves ramen", confidence=0.9, privacy_tier="share"))
    db_session.commit()

    def fake_stream(*args, **kwargs):
        yield "Try "
        yield "ramen!"

    with patch("app.routes.call_llm_stream", side_effect=fake_stream):
        response = client.post("/api/chat", json={"message": "what should I eat for dinner?", "mode": "personalized"})

    assert response.status_code == 200
    assert response.text == "Try ramen!"
    assert response.headers["x-used-trait-ids"] != ""


def test_chat_compare_runs_both_modes(client):
    with patch("app.routes.call_llm", side_effect=["baseline answer", "personalized answer"]):
        response = client.post("/api/chat/compare", json={"message": "what should I eat?"})
    assert response.status_code == 200
    body = response.json()
    assert body["baseline"] == "baseline answer"
    assert body["personalized"] == "personalized answer"


# ---- qa ----


def test_qa_topics_lists_all_categories(client):
    response = client.get("/api/qa/topics")
    assert response.status_code == 200
    topics = {t["topic"] for t in response.json()}
    assert "food" in topics and "music" in topics


def test_qa_start_and_answer_flow(client):
    with patch("app.routes.call_llm", return_value=QuestionResult(question="What's your favorite cuisine?")):
        start_response = client.post("/api/qa/start", json={"topic": "food"})
    assert start_response.status_code == 200
    session_id = start_response.json()["session_id"]

    with patch("app.routes.call_llm", return_value=QuestionResult(question="Any cuisine you avoid?")):
        answer_response = client.post("/api/qa/answer", json={"session_id": session_id, "answer": "I love Japanese food"})
    assert answer_response.status_code == 200
    assert answer_response.json()["done"] is False


# ---- privacy ----


def test_get_privacy_returns_category_defaults(client):
    response = client.get("/api/privacy")
    assert response.status_code == 200
    assert response.json()["category_defaults"]["food"] == "share"


def test_set_category_tier_cascades_to_non_overridden_traits(client, db_session, user):
    trait = Trait(user_id=user.id, category="fitness", key="k1", value="Runs often", confidence=0.8, privacy_tier="ask")
    db_session.add(trait)
    db_session.commit()
    db_session.refresh(trait)

    response = client.put("/api/privacy/category/fitness", json={"tier": "private"})
    assert response.status_code == 200
    db_session.refresh(trait)
    assert trait.privacy_tier == "private"


def test_generate_and_check_mcp_token(client, user):
    response = client.post("/api/mcp/token")
    assert response.status_code == 200
    assert "token" in response.json()
    assert user.mcp_token_hash is not None


def test_mcp_log_returns_empty_list_initially(client):
    response = client.get("/api/mcp/log")
    assert response.status_code == 200
    assert response.json() == []


# ---- usage ----


def test_get_usage_aggregates_model_calls(client, db_session, user):
    db_session.add(
        ModelCall(user_id=user.id, task="chat", model="super", model_id="x", input_tokens=100, output_tokens=50, cost_usd=0.01)
    )
    db_session.commit()

    response = client.get("/api/usage")
    assert response.status_code == 200
    body = response.json()
    assert body["total_calls"] == 1
    assert body["total_cost_usd"] == 0.01


def test_get_nudge_caches_per_day(client):
    fake_nudge = {"type": "suggestion", "message": "Try something new tonight!"}
    with patch("app.routes.generate_daily_nudge", return_value=fake_nudge) as mocked:
        first = client.get("/api/nudge")
        second = client.get("/api/nudge")

    assert first.status_code == 200
    assert first.json()["message"] == fake_nudge["message"]
    assert second.json()["cached"] is True
    mocked.assert_called_once()  # second call served from the daily cache


# ---- account ----


def test_delete_all_data_wipes_user_data(client, db_session, user):
    db_session.add(RawItem(user_id=user.id, provider="gmail", external_id="e1", item_type="email_receipt", payload_json={}))
    db_session.add(Fact(user_id=user.id, category="food", statement="Likes sushi", source_item_ids=[], model_used="nano"))
    db_session.add(Trait(user_id=user.id, category="food", key="k1", value="Loves ramen", confidence=0.9, privacy_tier="share"))
    db_session.add(Connection(user_id=user.id, provider="gmail", status="demo"))
    db_session.commit()

    response = client.delete("/api/account/data")
    assert response.status_code == 200
    assert db_session.query(RawItem).count() == 0
    assert db_session.query(Fact).count() == 0
    assert db_session.query(Trait).count() == 0
    assert db_session.query(Connection).count() == 0


