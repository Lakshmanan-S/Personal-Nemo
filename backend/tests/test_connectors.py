"""Unit tests for connectors' offline sample-data fallback (plan.md section 6)."""
import pytest
import httpx
import base64
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch


from app.connectors import all_providers, get_connector, GmailConnector, ConnectorError
from app.config import Settings




def test_calendar_oauth_uses_separate_read_only_scope_and_callback(gmail_settings):
    from app.connectors import GoogleCalendarConnector
    connector = GoogleCalendarConnector()
    query = parse_qs(urlparse(connector.authorize_url("calendar-state")).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/calendar.readonly"]
    assert query["redirect_uri"] == [gmail_settings.google_calendar_redirect_uri]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    bodies = []
    def handle(request):
        bodies.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token":"access","refresh_token":"refresh","expires_in":3600,
                                        "scope":connector.SCOPE,"token_type":"Bearer"})
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        assert connector.exchange_code("code", code_verifier="verifier").refresh_token == "refresh"
    assert bodies[0]["redirect_uri"] == [gmail_settings.google_calendar_redirect_uri]
    assert bodies[0]["code_verifier"] == ["verifier"]




def test_calendar_window_expands_instances_and_excludes_sensitive_fields(gmail_settings):
    from app.connectors import GoogleCalendarConnector
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"items":[
            {"id":"event1","status":"confirmed","summary":"Badminton","description":"DO NOT STORE",
             "start":{"dateTime":"2026-10-30T15:00:00+05:30"},"end":{"dateTime":"2026-10-30T16:00:00+05:30"}},
            {"id":"event2","status":"confirmed","summary":"Holiday","start":{"date":"2026-10-30"},"end":{"date":"2026-11-01"}},
            {"id":"event3","status":"cancelled"},
        ]})
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        snapshot = GoogleCalendarConnector().fetch_window("access", datetime(2026, 10, 30, 6, tzinfo=timezone.utc), "Asia/Kolkata")
    assert snapshot.complete and len(snapshot.items) == 3
    params = calls[0].url.params
    assert params["singleEvents"] == params["showDeleted"] == "true"
    assert params["orderBy"] == "startTime"
    assert params["timeMin"] == "2026-10-29T18:30:00+00:00"
    assert params["timeMax"] == "2026-11-05T18:30:00+00:00"
    assert "description" not in params["fields"] and "email" not in params["fields"]
    assert snapshot.items[0].payload["source"] == "gcal_live"
    assert "DO NOT STORE" not in str(snapshot.items)
    assert snapshot.items[1].payload["all_day"] is True
    assert snapshot.items[2].payload["status"] == "cancelled"




@pytest.mark.parametrize("event", [
    {"id":"bad","status":"confirmed","start":{"dateTime":"2026-10-30T15:00:00"},"end":{"dateTime":"2026-10-30T16:00:00"}},
    {"id":"bad","status":"confirmed","start":{"date":"2026-10-31"},"end":{"date":"2026-10-30"}},
    {"id":"bad","status":"confirmed","start":{"date":"2026-10-30"},"end":{"dateTime":"2026-10-30T16:00:00Z"}},
    {"id":"bad","status":"confirmed","start":{"dateTime":"2026-11-01T01:30:00","timeZone":"America/New_York"},
     "end":{"dateTime":"2026-11-01T03:00:00","timeZone":"America/New_York"}},
    {"id":"bad","status":"confirmed","start":{"dateTime":"2026-03-08T02:30:00","timeZone":"America/New_York"},
     "end":{"dateTime":"2026-03-08T04:00:00","timeZone":"America/New_York"}},
])
def test_calendar_rejects_malformed_times_without_a_partial_snapshot(gmail_settings, event):
    from app.connectors import GoogleCalendarConnector
    transport = httpx.MockTransport(lambda request: httpx.Response(200,json={"items":[event]}))
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=transport)):
        with pytest.raises(ConnectorError):
            GoogleCalendarConnector().fetch_window("access", datetime(2026,10,30,tzinfo=timezone.utc), "UTC")




def test_calendar_accepts_explicit_iana_wall_time(calendar_event):
    from app.connectors import CalendarEvent, _calendar_item
    from zoneinfo import ZoneInfo
    event = CalendarEvent.model_validate(calendar_event(
        start={"dateTime":"2026-10-30T15:00:00","timeZone":"Asia/Kolkata"},
        end={"dateTime":"2026-10-30T16:00:00","timeZone":"Asia/Kolkata"},
    ))
    item = _calendar_item(event,ZoneInfo("Asia/Kolkata"))
    assert item.payload["start_at"] == "2026-10-30T09:30:00+00:00"




@pytest.mark.parametrize("identity", [
    {"id":"calendar@example.com","timeZone":"Asia/Kolkata"},
    {"id":"not-a-calendar","timeZone":"UTC"},
    {"id":"calendar@example.com","timeZone":"invalid/zone"},
    {"id":"calendar@example.com"},
])
def test_calendar_validates_primary_identity(gmail_settings,identity):
    from app.connectors import GoogleCalendarConnector
    transport = httpx.MockTransport(lambda request: httpx.Response(200,json=identity))
    with patch("app.connectors.httpx.Client",return_value=httpx.Client(transport=transport)):
        if identity.get("timeZone") == "Asia/Kolkata":
            result = GoogleCalendarConnector().primary_calendar("access")
            assert result.id == identity["id"]
        else:
            with pytest.raises(ConnectorError,match="identity"):
                GoogleCalendarConnector().primary_calendar("access")




def test_calendar_pagination_and_hard_cap_report_partial_snapshot(gmail_settings,calendar_event):
    from app.connectors import GoogleCalendarConnector
    calls = []
    def handle(request):
        calls.append(request)
        first = not request.url.params.get("pageToken")
        return httpx.Response(200,json={
            "items":[calendar_event(f"event{i}") for i in range(0 if first else 50,50 if first else 100)],
            "nextPageToken":"next" if first else "more",
        })
    with patch("app.connectors.httpx.Client",return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        result = GoogleCalendarConnector().fetch_window("access",datetime(2026,10,30,tzinfo=timezone.utc),"America/New_York")
    assert len(result.items) == 100 and not result.complete and len(calls) == 2
    assert calls[1].url.params["maxResults"] == "50"
    assert (result.window_end-result.window_start).total_seconds() == 169*3600




@pytest.mark.parametrize("case",["repeated-page","duplicate-event","bad-link","empty-pages","malformed-second-page"])
def test_calendar_does_not_return_an_inconsistent_snapshot(gmail_settings,calendar_event,case):
    from app.connectors import GoogleCalendarConnector
    calls = []
    def handle(request):
        calls.append(request)
        index = len(calls)
        if case == "bad-link":
            return httpx.Response(200,json={"items":[calendar_event(htmlLink="https://calendar.google.com.evil.example/calendar")]})
        if case == "empty-pages":
            return httpx.Response(200,json={"items":[],"nextPageToken":str(index)})
        if index == 1:
            return httpx.Response(200,json={"items":[calendar_event()],"nextPageToken":"again"})
        if case == "malformed-second-page":
            return httpx.Response(200,json={"items":[{"id":"bad"}]})
        return httpx.Response(200,json={"items":[calendar_event("next" if case == "repeated-page" else "event1")],
                                       "nextPageToken":"again"})
    with patch("app.connectors.httpx.Client",return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        with pytest.raises(ConnectorError):
            GoogleCalendarConnector().fetch_window("access",datetime(2026,10,30,tzinfo=timezone.utc),"UTC")
    assert len(calls) <= 11




@pytest.mark.parametrize("provider", list(all_providers()))
def test_load_sample_data_returns_normalized_items(provider):
    connector = get_connector(provider)
    items = connector.load_sample_data()


    assert len(items) > 0
    for item in items:
        assert item.external_id
        assert item.item_type
        assert isinstance(item.payload, dict)
        assert item.occurred_at is not None




def test_load_sample_data_external_ids_are_unique_per_provider():
    for provider in all_providers():
        items = get_connector(provider).load_sample_data()
        external_ids = [item.external_id for item in items]
        assert len(external_ids) == len(set(external_ids))




@pytest.fixture()
def gmail_settings(monkeypatch):
    settings = Settings(
        _env_file=None, google_client_id="local-client", google_client_secret="local-secret",
        google_redirect_uri="http://localhost:8000/api/oauth/callback/gmail",
    )
    monkeypatch.setattr("app.connectors.get_settings", lambda: settings)
    return settings




def test_gmail_code_exchange_refresh_and_authorization(gmail_settings):
    connector = GmailConnector()
    query = parse_qs(urlparse(connector.authorize_url("random-state")).query)
    assert query["scope"] == [connector.SCOPE]
    assert query["state"] == ["random-state"]
    assert query["redirect_uri"] == [gmail_settings.google_redirect_uri]
    bodies = []
    def handle(request):
        bodies.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={
            "access_token":"access", "refresh_token":"refresh", "expires_in":3600,
            "scope":connector.SCOPE, "token_type":"Bearer",
        })
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        result = connector.exchange_code("code", code_verifier="verifier")
    assert result.access_token == "access" and result.refresh_token == "refresh"
    assert result.expires_at > datetime.now(timezone.utc)
    assert bodies[0]["code_verifier"] == ["verifier"]
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        assert connector.refresh("refresh").access_token == "access"
    assert bodies[-1]["grant_type"] == ["refresh_token"]




def test_gmail_fetch_is_bounded_paginated_and_ignores_attachments(gmail_settings):
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/messages"):
            page = request.url.params.get("pageToken")
            return httpx.Response(200, json={
                "messages":[{"id":str(i)} for i in range(13, 26)] if page else [{"id":str(i)} for i in range(13)],
                **({} if page else {"nextPageToken":"next"}),
            })
        return httpx.Response(200, json={
            "id":request.url.path.split("/")[-1], "internalDate":"1793120000000",
            "snippet":"Booking confirmed", "payload":{"headers":[{"name":"Subject","value":"Receipt"}],
                "parts":[{"mimeType":"text/plain","body":{"data":base64.urlsafe_b64encode(b"Your booking").decode()}},
                         {"mimeType":"text/plain","filename":"secret.txt","body":{"attachmentId":"never-fetch"}}]},
        })
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        items = GmailConnector().fetch_since("access", None)
    assert len(items) == 25
    assert len([r for r in calls if r.url.path.endswith("/messages")]) == 2
    assert "after:" in calls[0].url.params["q"] and "receipt" in calls[0].url.params["q"]
    query = calls[0].url.params["q"]
    assert all(term in query for term in ('deadline', 'EOD', '"please complete"', '"please review"', '"please submit"'))
    assert len(calls) == 27 and not any("attachments" in str(r.url) for r in calls)
    assert items[0].payload["body_text"] == "Your booking"
    assert items[0].payload["source"] == "gmail_live"
    assert items[0].external_id.startswith("gmail_live:")




def test_gmail_action_search_uses_bounded_window(gmail_settings):
    now = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"messages":[]})
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))), patch(
        "app.connectors.datetime", wraps=datetime,
    ) as clock:
        clock.now.return_value = now
        assert GmailConnector().fetch_since("access", None) == []
    query = calls[0].url.params["q"]
    cutoffs = [int(value) for value in re.findall(r"after:(\d+)", query)]
    assert cutoffs == [int((now - timedelta(days=30)).timestamp())]
    assert "EOD" in query and query.endswith("-in:chats")
    assert len(calls) == 1




@pytest.mark.parametrize("status,expected", [
    (400,"authorization"), (401,"authorization"), (403,"denied"), (429,"rate limit"), (500,"error"),
])
def test_gmail_provider_errors_are_safe(gmail_settings, status, expected):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"error":"SECRET_PROVIDER_DETAIL"}))
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=transport)):
        with pytest.raises(ConnectorError) as caught:
            GmailConnector().exchange_code("code")
    assert expected in str(caught.value)
    assert "SECRET_PROVIDER_DETAIL" not in str(caught.value)
    assert caught.value.reconnect == (status in (400,401))




@pytest.mark.parametrize("kind", ["network", "not-json", "not-object", "bad-token", "missing-scope", "wrong-scope", "wrong-type"])
def test_gmail_rejects_malformed_and_unauthorized_responses(gmail_settings, kind):
    def handle(request):
        if kind == "network":
            raise httpx.ConnectError("sensitive URL detail", request=request)
        if kind == "not-json":
            return httpx.Response(200, text="not JSON")
        if kind == "not-object":
            return httpx.Response(200, json=[])
        data = {"access_token":"access", "expires_in":3600, "token_type":"Bearer", "scope":GmailConnector.SCOPE}
        if kind == "bad-token":
            data["access_token"] = ""
        if kind == "missing-scope":
            del data["scope"]
        if kind == "wrong-scope":
            data["scope"] = "openid"
        if kind == "wrong-type":
            data["token_type"] = "Basic"
        return httpx.Response(200, json=data)
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        with pytest.raises(ConnectorError):
            GmailConnector().exchange_code("code")




@pytest.mark.parametrize("email", ["valid@example.com", None, "invalid"])
def test_gmail_mailbox_identity(gmail_settings, email):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"emailAddress":email}))
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=transport)):
        if email == "valid@example.com":
            assert GmailConnector().mailbox_email("access") == email
        else:
            with pytest.raises(ConnectorError):
                GmailConnector().mailbox_email("access")




def test_gmail_revocation_uses_form_and_accepts_empty_success(gmail_settings):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200)
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        GmailConnector().revoke("refresh-secret")
    assert seen[0].url == "https://oauth2.googleapis.com/revoke"
    assert seen[0].content == b"token=refresh-secret"




def test_gmail_already_revoked_token_can_disconnect(gmail_settings):
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error":"invalid_token"}))
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=transport)):
        GmailConnector().revoke("already-revoked")




@pytest.mark.parametrize("case", ["empty", "wrong-id", "malformed-date", "repeated-page", "duplicate-id", "bad-base64", "html-only", "large-text"])
def test_gmail_fetch_edge_cases(gmail_settings, case):
    listed = 0
    def handle(request):
        nonlocal listed
        if request.url.path.endswith("/messages"):
            listed += 1
            if case == "empty":
                return httpx.Response(200, json={})
            if case == "duplicate-id":
                return httpx.Response(200, json={"messages":[{"id":"one"}, {"id":"one"}]})
            if case == "repeated-page":
                return httpx.Response(200, json={"messages":[{"id":"one"}], "nextPageToken":"same"})
            return httpx.Response(200, json={"messages":[{"id":"one"}]})
        body = base64.urlsafe_b64encode(("x"*20000 if case == "large-text" else "Receipt").encode()).decode()
        return httpx.Response(200, json={
            "id": "other" if case == "wrong-id" else "one",
            "internalDate": "bad" if case == "malformed-date" else "1793120000000",
            "payload":{"mimeType":"text/html" if case == "html-only" else "text/plain",
                       "body":{"data":"***" if case == "bad-base64" else body}},
        })
    with patch("app.connectors.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handle))):
        if case in ("wrong-id", "malformed-date", "repeated-page", "bad-base64"):
            with pytest.raises(ConnectorError):
                GmailConnector().fetch_since("access", datetime.now(timezone.utc))
        else:
            items = GmailConnector().fetch_since("access", None)
            assert len(items) == (0 if case == "empty" else 1)
            if case == "html-only":
                assert items[0].payload["body_text"] == ""
            if case == "large-text":
                assert len(items[0].payload["body_text"]) == 6000
    assert listed <= 2



