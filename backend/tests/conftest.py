"""Shared pytest fixtures: an isolated in-memory SQLite DB per test."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


from app.db import Base, User, get_db
from app.routes import get_current_user




@pytest.fixture()
def gmail_config(monkeypatch):
    from app.config import Settings
    from cryptography.fernet import Fernet
    settings = Settings(
        _env_file=None, google_client_id="local-client", google_client_secret="local-secret",
        token_encryption_key=Fernet.generate_key().decode(),
        google_redirect_uri="http://testserver/api/oauth/callback/gmail",
        google_calendar_redirect_uri="http://testserver/api/oauth/callback/gcal",
    )
    for module in ("app.routes", "app.services", "app.connectors", "app.pipeline"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return settings




@pytest.fixture()
def tavily_http(monkeypatch):
    import httpx
    from app import services
    from app.config import Settings
    settings = Settings(_env_file=None,TAVIL_API_KEY="isolated-tavily-test-key")
    monkeypatch.setattr(services,"get_settings",lambda:settings)
    real_client = httpx.Client
    def install(body, status=200, network_error=False):
        requests = []
        def handle(request):
            requests.append(request)
            if network_error:
                raise httpx.ConnectError("private provider details",request=request)
            return httpx.Response(status,json=body)
        monkeypatch.setattr(services.httpx,"Client",lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
        return requests
    return install




@pytest.fixture()
def calendar_event():
    def create(event_id="event1", **changes):
        return {
            "id":event_id, "status":"confirmed", "summary":"Badminton",
            "start":{"dateTime":"2026-10-30T15:00:00+05:30"},
            "end":{"dateTime":"2026-10-30T16:00:00+05:30"},
            "htmlLink":"https://calendar.google.com/calendar/event?eid=test", **changes,
        }
    return create




@pytest.fixture()
def calendar_raw(db_session, user, calendar_event):
    from zoneinfo import ZoneInfo
    from app.connectors import CalendarEvent, _calendar_item
    from app.db import RawItem
    def create(event_id="event1", **changes):
        source = _calendar_item(CalendarEvent.model_validate(calendar_event(event_id,**changes)),ZoneInfo("Asia/Kolkata"))
        raw = RawItem(user_id=user.id,provider="gcal",external_id="gcal_live:calendar@example.com:"+event_id,
                      item_type="calendar_event",payload_json=source.payload,occurred_at=source.occurred_at,processed=True)
        db_session.add(raw)
        db_session.commit()
        return raw
    return create




@pytest.fixture()
def calendar_model(gmail_config):
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    gmail_config.nebius_api_key = "isolated-test-key"
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    def respond(**kwargs):
        labels = []
        for event in json.loads(kwargs["messages"][1]["content"]):
            title = event["title"]
            if "Badminton" in title:
                kind, activity, quote = "fitness","badminton","Badminton"
            elif "Client" in title:
                kind, activity, quote = "work","work","Client"
            elif "Study" in title:
                kind, activity, quote = "study","study","Study"
            else:
                assert title == "Private appointment"
                kind, activity, quote = "ignore","none",""
            labels.append({"event_id":event["event_id"],"kind":kind,"activity":activity,"quote":quote})
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop",message=SimpleNamespace(content=json.dumps({"events":labels})))],
            usage=SimpleNamespace(prompt_tokens=10,completion_tokens=5),
        )
    call.side_effect = respond
    with patch("app.pipeline._client",return_value=fake):
        yield call




@pytest.fixture()
def calendar_sync(db_session, user, gmail_config):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from zoneinfo import ZoneInfo
    from app import services
    from app.connectors import CalendarEvent, CalendarIdentity, CalendarSnapshot, TokenBundle, _calendar_item
    identity = CalendarIdentity(id="calendar@example.com", timeZone="Asia/Kolkata")
    now = datetime(2026,10,30,6,tzinfo=timezone.utc)
    services.save_calendar_connection(db_session,user.id,TokenBundle("access","refresh",now+timedelta(days=365)),identity)
    def sync(events, complete=True, clock=now):
        zone = ZoneInfo(identity.timeZone)
        start = clock.astimezone(zone).replace(hour=0,minute=0,second=0,microsecond=0)
        snapshot = CalendarSnapshot(
            [_calendar_item(CalendarEvent.model_validate(event),zone) for event in events],
            start.astimezone(timezone.utc), (start+timedelta(days=7)).astimezone(timezone.utc), complete,
        )
        with patch.object(services,"_today_now",return_value=clock), patch.object(
            services.GoogleCalendarConnector,"primary_calendar",return_value=identity,
        ), patch.object(services.GoogleCalendarConnector,"fetch_window",return_value=snapshot):
            return services.sync_calendar(db_session,user.id)
    return sync




@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()




@pytest.fixture()
def user(db_session):
    u = User(email="test@example.com")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u




@pytest.fixture()
def today_email(db_session, user):
    from datetime import datetime, timezone
    from app.db import RawItem


    def create(body="Please review the proposal today, 2026-10-30, by 5 PM.", external_id=None, received=None):
        import uuid
        item = RawItem(
            user_id=user.id, provider="gmail",
            external_id=external_id or f"gmail_live:test@example.invalid:{uuid.uuid4().hex}",
            item_type="email_receipt", processed=True,
            occurred_at=received or datetime(2026, 10, 30, 6, tzinfo=timezone.utc),
            payload_json={"source":"gmail_live", "subject":"Review request", "body_text":body},
        )
        db_session.add(item)
        db_session.commit()
        return item
    return create




@pytest.fixture()
def today_candidate():
    def create(**updates):
        return {
            "kind":"action", "title":"Review proposal draft",
            "detail":"The sender asked you to review the proposal today.",
            "day":"2026-10-30", "at":None,
            "quote":"Please review the proposal", "when_quote":"today, 2026-10-30, by 5 PM",
            "reference":None, "operation":"upsert", **updates,
        }
    return create




@pytest.fixture()
def today_deadline(today_candidate):
    body = (
        "Hi there,\nThe document which u have to send on architectural diagram has to be done by EOD. "
        "If not meeting the deadline, it will go as an escalation to client. Please complete it by today.\n"
        "Thanks and Regards,\nABC Consulting"
    )
    return body, today_candidate(
        title="Send architectural diagram",
        detail="ABC Consulting asked you to send the architectural diagram today to avoid a client escalation.",
        quote="The document which u have to send on architectural diagram has to be done by EOD.",
        when_quote="Please complete it by today.",
    )




@pytest.fixture()
def client(db_session, user):
    from app.main import app


    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()



