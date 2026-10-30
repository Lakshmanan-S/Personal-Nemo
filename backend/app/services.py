"""
Supporting services in one module: token encryption at rest, demo-user
bootstrapping, relevant-trait retrieval for Chat/MCP, and demo data loading
(connectors -> raw_items -> full Nemotron pipeline).
"""
from datetime import datetime, timedelta, timezone
import uuid
import hashlib
import re
import unicodedata
from functools import lru_cache
from typing import Callable, TypeVar
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors import CalendarIdentity, ConnectorError, GmailConnector, GoogleCalendarConnector, GoogleOAuthConnector, TokenBundle, all_providers, get_connector
from app.config import DEFAULT_CATEGORY_TIER, Category
from app.db import BlockedTrait, CalendarAccount, CalendarLearning, Connection, EmailIngestion, EmailProcessingLease, Fact, GmailAccount, LifeContext, MCPApproval, RawItem, TodayItem, TodayScan, Trait, User
from app.demo_seed_data import DEMO_SEED_FACTS, DEMO_SEED_TRAITS
from app.pipeline import BudgetExceededError, EmailProcessingError, EVENT_CATEGORIES, FOOD_GROUP_LABELS, calendar_source_hash, classify_calendar_events, classify_imported_emails, extract_email_event, extract_one_imported_email, extract_today_candidates, food_concept, run_extraction_for_user, run_privacy_tagging_for_user, run_synthesis_for_user

# ---------------------------------------------------------------------------
# Public web search
# ---------------------------------------------------------------------------


class WebSearchError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class WebSearchHit(BaseModel):
    model_config = ConfigDict(strict=True)
    title: str = Field(min_length=1, max_length=5000)
    url: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=20000)

    @field_validator("url")
    @classmethod
    def public_link(cls, value: str) -> str:
        parsed = urlparse(value)
        if (parsed.scheme not in ("https","http") or not parsed.hostname or parsed.username or parsed.password
                or any(character.isspace() or ord(character) < 32 for character in value)):
            raise ValueError("Invalid web source URL.")
        try:
            normalized = str(httpx.URL(value))
        except httpx.InvalidURL:
            raise ValueError("Invalid web source URL.") from None
        if len(normalized.encode("ascii")) > 1000:
            raise ValueError("Web source URL is too long.")
        return normalized

    def source(self) -> dict[str, str]:
        return {"title":self.title,"url":self.url}


class WebSearchResponse(BaseModel):
    results: list[WebSearchHit] = Field(max_length=3)


def search_public_web(query: str) -> list[WebSearchHit]:
    query = query.strip()
    if not query or len(query) > 300:
        raise WebSearchError("Enter a public search query of 1-300 characters.",status_code=422)
    key = get_settings().tavily_api_key
    if not key:
        raise WebSearchError("Web search is not configured. Set TAVIL_API_KEY and restart the backend.",status_code=503)
    try:
        with httpx.Client(timeout=10.0,follow_redirects=False) as client:
            response = client.post("https://api.tavily.com/search",headers={"Authorization":f"Bearer {key}"},json={
                "query":query,"search_depth":"basic","topic":"general","max_results":3,"chunks_per_source":1,
                "auto_parameters":False,"include_answer":False,"include_raw_content":False,"include_images":False,
            })
    except httpx.RequestError:
        raise WebSearchError("Tavily could not be reached. No automatic retry or model request was made.") from None
    if response.status_code != 200:
        if response.status_code in (401,403):
            message = "Tavily authentication failed. Check the configured API key."
        elif response.status_code in (429,432,433):
            message = "Tavily rate or credit limit reached. Check your Tavily account before retrying."
        else:
            message = "Tavily search failed. Try again later."
        raise WebSearchError(message + " No automatic retry or model request was made.")
    try:
        result = WebSearchResponse.model_validate(response.json())
    except (ValueError,ValidationError):
        raise WebSearchError("Tavily returned invalid search results. No model request was made.") from None
    return [WebSearchHit(title=hit.title[:160],url=hit.url,content=hit.content[:1200]) for hit in result.results]


# ---------------------------------------------------------------------------
# Token encryption at rest (plan.md section 11)
# ---------------------------------------------------------------------------


@lru_cache
def _dev_fallback_key() -> bytes:
    """Generated once per process and cached, so encrypt/decrypt stay consistent
    for the lifetime of a run even when TOKEN_ENCRYPTION_KEY isn't set yet."""
    return Fernet.generate_key()


def _get_fernet() -> Fernet:
    settings = get_settings()
    key = settings.token_encryption_key
    key_bytes = key.encode() if key else _dev_fallback_key()
    return Fernet(key_bytes)


def encrypt_token(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_token(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


def validate_gmail_config(provider: str = "gmail") -> None:
    settings = get_settings()
    label = "Google Calendar" if provider == "gcal" else "Gmail"
    if not settings.google_client_id or not settings.google_client_secret:
        raise ConnectorError(f"Configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET before connecting {label}.")
    if not settings.token_encryption_key:
        raise ConnectorError(f"Set a persistent TOKEN_ENCRYPTION_KEY before connecting {label}.")
    try:
        Fernet(settings.token_encryption_key.encode())
    except (ValueError, TypeError):
        raise ConnectorError("TOKEN_ENCRYPTION_KEY is invalid. Configure a Fernet key locally.") from None
    from urllib.parse import urlparse
    callback = urlparse(settings.google_calendar_redirect_uri if provider == "gcal" else settings.google_redirect_uri)
    if callback.scheme not in ("http", "https") or not callback.netloc or callback.path != f"/api/oauth/callback/{provider}" or callback.query or callback.fragment:
        variable = "GOOGLE_CALENDAR_REDIRECT_URI" if provider == "gcal" else "GOOGLE_REDIRECT_URI"
        raise ConnectorError(f"{variable} must point to /api/oauth/callback/{provider}.")


def decrypt_gmail_token(ciphertext: str) -> str:
    return decrypt_google_token(ciphertext, "Gmail")


def decrypt_google_token(ciphertext: str, label: str) -> str:
    try:
        return decrypt_token(ciphertext)
    except (InvalidToken, ValueError):
        raise ConnectorError(f"Stored {label} tokens cannot be decrypted. Restore the original encryption key or reconnect {label}.", reconnect=True) from None


def save_gmail_connection(db: Session, user_id: str, tokens: TokenBundle, email: str) -> None:
    connection = _get_or_create_connection(db, user_id, "gmail")
    account = db.get(GmailAccount, user_id)
    same_account = account is not None and account.email == email
    if account is not None and not same_account:
        raise ConnectorError("A different Gmail account is connected. Disconnect it before switching mailboxes.")
    if account is None:
        db.add(GmailAccount(user_id=user_id, email=email))
    _save_google_tokens(db, connection, tokens, same_account, "Gmail")


def _save_google_tokens(db: Session, connection: Connection, tokens: TokenBundle, same_account: bool, label: str) -> None:
    refresh = tokens.refresh_token
    if not refresh and same_account and connection.encrypted_refresh_token:
        refresh = decrypt_google_token(connection.encrypted_refresh_token, label)
    if not refresh:
        raise ConnectorError(f"Google did not issue offline access. Reconnect {label} and grant consent again.", reconnect=True)
    connection.encrypted_access_token = encrypt_token(tokens.access_token)
    connection.encrypted_refresh_token = encrypt_token(refresh)
    connection.expires_at = tokens.expires_at
    connection.status = "connected"
    db.commit()


def save_calendar_connection(db: Session, user_id: str, tokens: TokenBundle, identity: CalendarIdentity) -> None:
    try:
        today_zone(identity.timeZone)
    except ValueError:
        raise ConnectorError("Google Calendar returned an unsupported timezone.") from None
    connection = _get_or_create_connection(db, user_id, "gcal")
    account = db.get(CalendarAccount, user_id)
    same = account is not None and account.calendar_id == identity.id.lower()
    if account is not None and not same:
        raise ConnectorError("A different primary calendar is connected. Disconnect before switching accounts.")
    if account is None:
        account = CalendarAccount(user_id=user_id, calendar_id=identity.id.lower(), timezone=identity.timeZone)
        db.add(account)
    account.timezone = identity.timeZone
    _save_google_tokens(db, connection, tokens, same, "Google Calendar")


_GoogleResult = TypeVar("_GoogleResult")


def _google_fetch(db: Session, connection: Connection, connector: GoogleOAuthConnector,
                  fetch: Callable[[str], _GoogleResult]) -> _GoogleResult:
    def refresh_access() -> str:
        token = connector.refresh(decrypt_google_token(connection.encrypted_refresh_token or "", connector.LABEL))
        connection.encrypted_access_token = encrypt_token(token.access_token)
        if token.refresh_token:
            connection.encrypted_refresh_token = encrypt_token(token.refresh_token)
        connection.expires_at = token.expires_at
        db.commit()
        return token.access_token

    expiry = connection.expires_at
    if not connection.encrypted_access_token or expiry is None or _utc(expiry) <= datetime.now(timezone.utc) + timedelta(seconds=60):
        access = refresh_access()
    else:
        access = decrypt_google_token(connection.encrypted_access_token, connector.LABEL)
    try:
        return fetch(access)
    except ConnectorError as exc:
        if not exc.reconnect:
            raise
        return fetch(refresh_access())


def sync_gmail(db: Session, user_id: str) -> dict:
    validate_gmail_config()
    connection = db.execute(select(Connection).where(
        Connection.user_id == user_id, Connection.provider == "gmail"
    )).scalar_one_or_none()
    account = db.get(GmailAccount, user_id)
    if connection is None or account is None or not connection.encrypted_refresh_token:
        raise ConnectorError("Connect Gmail before syncing. Use Load sample data for the offline demo.", reconnect=True)
    connector = GmailConnector()

    items = _google_fetch(db, connection, connector, lambda access: connector.fetch_since(access, None))
    prefix = f"gmail_live:{account.email}:"
    existing = set(db.execute(select(RawItem.external_id).where(
        RawItem.user_id == user_id, RawItem.provider == "gmail"
    )).scalars())
    inserted = 0
    for item in items:
        external_id = prefix + item.external_id.removeprefix("gmail_live:")
        if external_id in existing:
            continue
        existing.add(external_id)
        db.add(RawItem(
            user_id=user_id, provider="gmail", external_id=external_id, item_type=item.item_type,
            payload_json=item.payload, occurred_at=item.occurred_at, processed=False,
        ))
        inserted += 1
    connection.status = "connected"
    connection.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    pending = db.execute(select(RawItem.id).where(
        RawItem.user_id == user_id, RawItem.provider == "gmail", RawItem.processed.is_(False),
        RawItem.external_id.startswith("gmail_live:"),
    )).scalars()
    return {"provider": "gmail", "mode": "live", "messages_fetched": len(items),
            "raw_items_inserted": inserted, "pending_items": len(list(pending)),
            "limit_reached": len(items) == 25,
            "message": "Emails imported locally. No AI processing or model charges. Only the bounded 30-day search window was checked."}


def remove_fact_evidence(db: Session, user_id: str, fact_id: str) -> None:
    fact = db.get(Fact,fact_id)
    if fact and fact.user_id == user_id:
        db.delete(fact)
    for trait in db.scalars(select(Trait).where(Trait.user_id == user_id)):
        if fact_id in trait.supporting_fact_ids:
            trait.supporting_fact_ids = [entry for entry in trait.supporting_fact_ids if entry != fact_id]
            trait.evidence_count = len(trait.supporting_fact_ids)
            trait.privacy_tier = "private"
            if trait.user_overridden:
                trait.summary = "Evidence removed or changed. Reviewed snapshot retained privately; review its value and remaining evidence."


def _update_calendar_raw(db: Session, raw: RawItem, payload: dict) -> None:
    raw.payload_json = payload
    raw.processed = True
    db.flush()
    fingerprint = calendar_source_hash(raw)
    scan = db.get(CalendarLearning,raw.id)
    if scan is None:
        scan = CalendarLearning(raw_item_id=raw.id,user_id=raw.user_id,source_hash=fingerprint)
        db.add(scan)
    if scan.source_hash != fingerprint:
        if scan.fact_id:
            remove_fact_evidence(db,raw.user_id,scan.fact_id)
        scan.fact_id, scan.candidate = None, None
        scan.source_hash = fingerprint
        if scan.status != "removed":
            scan.status = "pending"
    active = payload.get("status") == "confirmed"
    if scan.status not in ("learned","removed","ignored","failed"):
        scan.status = "pending" if active and payload.get("title","").strip() and payload.get("event_type") == "default" else "ignored"
    key = "gcal:" + raw.id
    reminder = db.scalar(select(TodayItem).where(TodayItem.user_id == raw.user_id,TodayItem.event_key == key))
    if reminder is None and not active:
        return
    if reminder is None:
        reminder = TodayItem(user_id=raw.user_id,event_key=key,kind="reminder")
        db.add(reminder)
    reminder.title = (payload.get("title") or "Calendar event")[:70]
    reminder.detail = ""
    reminder.day = payload.get("start_date","")
    reminder.starts_at = None if payload.get("all_day") or not payload.get("start_at") else datetime.fromisoformat(payload["start_at"])
    reminder.source_item_ids = [raw.id]
    if reminder.status not in ("done","dismissed"):
        reminder.status = "pending" if active else "cancelled"


def sync_calendar(db: Session, user_id: str) -> dict:
    validate_gmail_config("gcal")
    connection = db.scalar(select(Connection).where(Connection.user_id == user_id,Connection.provider == "gcal"))
    account = db.get(CalendarAccount,user_id)
    if connection is None or account is None or not connection.encrypted_refresh_token:
        raise ConnectorError("Connect Google Calendar before syncing. No sample fallback was used.",reconnect=True)
    connector = GoogleCalendarConnector()
    now = _today_now()
    def fetch(access: str):
        identity = connector.primary_calendar(access)
        if identity.id.lower() != account.calendar_id:
            raise ConnectorError("Google Calendar account changed. Disconnect before switching accounts.",reconnect=True)
        return identity, connector.fetch_window(access,now,identity.timeZone)
    identity, snapshot = _google_fetch(db,connection,connector,fetch)
    account.timezone = identity.timeZone
    prefix = f"gcal_live:{account.calendar_id}:"
    existing = {raw.external_id:raw for raw in db.scalars(select(RawItem).where(
        RawItem.user_id == user_id,RawItem.provider == "gcal",RawItem.external_id.startswith(prefix),
    ))}
    inserted, updated = 0, 0
    seen = set()
    for item in snapshot.items:
        external_id = prefix + item.external_id.removeprefix("gcal_live:")
        seen.add(external_id)
        raw = existing.get(external_id)
        if raw is None:
            raw = RawItem(user_id=user_id,provider="gcal",external_id=external_id,item_type="calendar_event",payload_json={})
            db.add(raw)
            inserted += 1
        payload = {**(raw.payload_json or {}),**item.payload}
        if raw.payload_json != payload:
            updated += 1
        if item.occurred_at:
            raw.occurred_at = item.occurred_at
        _update_calendar_raw(db,raw,payload)
    withdrawn = 0
    if snapshot.complete:
        for external_id, raw in existing.items():
            payload = raw.payload_json
            if external_id in seen or payload.get("status") not in ("confirmed","tentative","declined"):
                continue
            if (datetime.fromisoformat(payload["end_at"]) > snapshot.window_start
                    and datetime.fromisoformat(payload["start_at"]) < snapshot.window_end):
                _update_calendar_raw(db,raw,{**payload,"status":"missing"})
                withdrawn += 1
    db.flush()
    refresh_calendar_traits(db,user_id)
    connection.status, connection.last_synced_at = "connected",now
    db.commit()
    return {
        "provider":"gcal","mode":"live","events_fetched":len(snapshot.items),"raw_items_inserted":inserted,
        "events_updated":updated,"events_withdrawn":withdrawn,"limit_reached":not snapshot.complete,
        "window_start":snapshot.window_start.isoformat(),"window_end":snapshot.window_end.isoformat(),
        "message":f"Synced {len(snapshot.items)} primary-calendar events for seven calendar days. Today reminders updated; no model charges."
                  + (" Partial snapshot: the 100-event limit was reached; missing events were not removed." if not snapshot.complete else ""),
    }


def process_calendar_learning(db: Session, user_id: str) -> dict:
    owner = _acquire_email_lease(db,user_id)
    try:
        items = list(db.scalars(select(RawItem).join(CalendarLearning,CalendarLearning.raw_item_id == RawItem.id).where(
            RawItem.user_id == user_id,CalendarLearning.user_id == user_id,CalendarLearning.status.in_(("pending","failed")),
        ).order_by(CalendarLearning.candidate.is_not(None),RawItem.occurred_at,RawItem.id).limit(5)))
        if not items:
            return {"events_checked":0,"results":[],"message":"No Calendar events awaiting learning. No model call made."}
        try:
            labels = classify_calendar_events(db,user_id,items)
        except (EmailProcessingError,BudgetExceededError) as exc:
            db.rollback()
            for raw in items:
                scan = db.get(CalendarLearning,raw.id)
                if scan:
                    scan.status = "failed"
            db.commit()
            return {"events_checked":len(items),"results":[{"event_id":raw.id,"status":"failed","error":str(exc)} for raw in items],
                    "message":"Calendar learning failed. Saved candidates retained for review; no automatic retry."}
        lease = db.get(EmailProcessingLease,user_id)
        if lease is None or lease.owner != owner or _utc(lease.expires_at) <= datetime.now(timezone.utc):
            raise ConnectorError("Calendar processing ownership changed. Saved candidates retained; no profile updated.")
        results = []
        for raw in items:
            scan = db.get(CalendarLearning,raw.id)
            if scan is None or scan.source_hash != calendar_source_hash(raw):
                raise ConnectorError("Calendar source changed during learning. No profile updated; sync and try again.")
            label = labels[raw.id]
            scan.candidate = {**(scan.candidate or {}),"label":label}
            scan.status = "ignored" if label["kind"] == "ignore" else "learned"
            if label["kind"] != "ignore":
                category = "fitness" if label["kind"] == "fitness" else "routine"
                activity = label["activity"] if category == "fitness" else ("work meetings or blocks" if label["kind"] == "work" else "study sessions")
                fact = Fact(user_id=user_id,category=category,statement=f"Scheduled {activity}; attendance is not confirmed.",
                            source_item_ids=[raw.id],model_used="nemotron-super-calendar",privacy_tier="private",
                            tier_reason="Calendar plans; private by default.",synthesized=True)
                db.add(fact)
                db.flush()
                scan.fact_id = fact.id
            results.append({"event_id":raw.id,"status":scan.status})
        db.flush()
        refresh_calendar_traits(db,user_id)
        db.commit()
        return {"events_checked":len(items),"results":results,
                "message":f"Checked {len(items)} saved Calendar events. Private planned-pattern cards updated; no attendance inferred."}
    finally:
        _release_email_lease(db,user_id,owner)


def refresh_calendar_traits(db: Session, user_id: str) -> None:
    groups: dict[str,list[tuple[RawItem,CalendarLearning]]] = {}
    for raw, scan in db.execute(select(RawItem,CalendarLearning).join(
        CalendarLearning,CalendarLearning.raw_item_id == RawItem.id,
    ).where(RawItem.user_id == user_id,CalendarLearning.user_id == user_id,CalendarLearning.status == "learned")):
        if scan.fact_id and raw.payload_json.get("status") == "confirmed" and scan.candidate:
            label = scan.candidate["label"]
            groups.setdefault(label["activity"],[]).append((raw,scan))
    blocked = set(db.execute(select(BlockedTrait.category,BlockedTrait.key).where(BlockedTrait.user_id == user_id)).all())
    traits = {trait.key:trait for trait in db.scalars(select(Trait).where(Trait.user_id == user_id,Trait.key.startswith("calendar_")))}
    for key, trait in traits.items():
        if key.removeprefix("calendar_").replace("_"," ") not in groups and not trait.user_overridden:
            db.delete(trait)
    for activity, entries in groups.items():
        key = "calendar_" + activity.replace(" ","_")
        category = "routine" if activity in ("work","study") else "fitness"
        trait = traits.get(key)
        if (category,key) in blocked or (trait and trait.user_overridden):
            continue
        if trait is None:
            trait = Trait(user_id=user_id,category=category,key=key)
            db.add(trait)
        facts = sorted(scan.fact_id for _, scan in entries if scan.fact_id)
        weekdays: dict[int,int] = {}
        periods: dict[str,int] = {}
        for raw, _ in entries:
            start = datetime.fromisoformat(raw.payload_json["start_at"]).astimezone(today_zone(raw.payload_json["timezone"]))
            weekdays[start.weekday()] = weekdays.get(start.weekday(),0) + 1
            if not raw.payload_json["all_day"]:
                period = "morning" if 5 <= start.hour < 12 else "afternoon" if 12 <= start.hour < 17 else "evening" if 17 <= start.hour < 22 else "night"
                periods[period] = periods.get(period,0) + 1
        days = [("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")[day]
                for day in sorted(weekdays,key=lambda day:(-weekdays[day],day))[:3]]
        name = "work meetings or blocks" if activity == "work" else "study sessions" if activity == "study" else activity
        wording = "Has planned" if len(entries) == 1 else "Schedules"
        trait.value = wording + " " + name + " on " + ", ".join(days) + "."
        if periods:
            maximum = max(periods.values())
            leaders = [period for period,count in periods.items() if count == maximum]
            if len(leaders) == 1 and maximum/len(entries) >= 0.6:
                trait.value = trait.value[:-1] + ", in the " + leaders[0] + "."
        trait.supporting_fact_ids, trait.evidence_count = facts,len(facts)
        trait.confidence = round(min(0.8,0.3 + 0.04*(len(facts)-1)),2)
        trait.privacy_tier = "private"
        trait.tier_reason = "Planned Calendar pattern, not attendance or a confirmed preference. Review before sharing."
        trait.summary = f"Based on {len(facts)} scheduled Calendar event(s), not confirmed attendance. Calendar and Gmail booking counts are kept separate. Reviewed snapshots are not automatically rewritten."


def _acquire_email_lease(db: Session, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    owner = str(uuid.uuid4())
    db.execute(delete(EmailProcessingLease).where(
        EmailProcessingLease.user_id == user_id, EmailProcessingLease.expires_at <= now
    ))
    db.add(EmailProcessingLease(user_id=user_id, owner=owner, expires_at=now + timedelta(minutes=15)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConnectorError("Another import or processing request is already in progress. Wait before retrying.") from None
    return owner


def _release_email_lease(db: Session, user_id: str, owner: str) -> None:
    db.rollback()
    db.execute(delete(EmailProcessingLease).where(
        EmailProcessingLease.user_id == user_id, EmailProcessingLease.owner == owner
    ))
    db.commit()


def _today_now() -> datetime:
    return datetime.now(timezone.utc)


def today_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unsupported timezone. Select a valid IANA timezone.") from None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _today_pending_query(user_id: str):
    return select(RawItem).outerjoin(TodayScan, TodayScan.raw_item_id == RawItem.id).where(
        RawItem.user_id == user_id, RawItem.provider == "gmail",
        RawItem.external_id.startswith("gmail_live:"),
        or_(TodayScan.raw_item_id.is_(None), TodayScan.status != "extracted"),
    )


def _today_event_key(user_id: str, raw: RawItem, candidate: dict) -> str:
    reference = candidate["reference"]
    if reference:
        identity = [raw.external_id.rsplit(":", 1)[0], _normalized((raw.payload_json or {}).get("from", "")),
                    reference["value"]]
        if candidate["kind"] == "action":
            identity.append(_normalized(candidate["title"]))
    else:
        identity = [raw.id, _normalized(candidate["title"]), candidate["day"], candidate["quote"], candidate["at"]]
    return hashlib.sha256(repr([user_id, candidate["kind"], *identity]).encode()).hexdigest()


def process_today_emails(db: Session, user_id: str, timezone_name: str) -> dict:
    today_zone(timezone_name)
    owner = _acquire_email_lease(db, user_id)
    results = []
    try:
        # New emails take precedence over failed saved candidates so review failures cannot block the queue.
        items = list(db.scalars(_today_pending_query(user_id).order_by(
            TodayScan.raw_item_id.is_not(None), RawItem.occurred_at.desc(), RawItem.id,
        ).limit(5)))
        if any((item.payload_json or {}).get("source") != "gmail_live" for item in items):
            raise EmailProcessingError("Invalid live source in Today queue; no calls made.")
        for raw in items:
            try:
                candidates = extract_today_candidates(db, user_id, raw, timezone_name)
            except (EmailProcessingError, BudgetExceededError) as exc:
                db.rollback()
                scan = db.get(TodayScan, raw.id)
                if scan is not None:
                    scan.status = "failed"
                    db.commit()
                results.append({"email_id": raw.id, "status": "failed", "error": str(exc)})
                if isinstance(exc, BudgetExceededError):
                    break
                continue
            lease = db.get(EmailProcessingLease, user_id)
            if lease is None or lease.owner != owner or _utc(lease.expires_at) <= datetime.now(timezone.utc):
                raise EmailProcessingError("Today processing ownership changed. Saved candidates retained; no items updated.")
            unmatched = 0
            for candidate in candidates:
                key = _today_event_key(user_id, raw, candidate)
                item = db.scalar(select(TodayItem).where(TodayItem.user_id == user_id, TodayItem.event_key == key))
                if item and raw.occurred_at:
                    prior_dates = list(db.scalars(select(RawItem.occurred_at).where(
                        RawItem.user_id == user_id, RawItem.id.in_(item.source_item_ids),
                        RawItem.occurred_at.is_not(None),
                    )))
                    if any(_utc(prior) > _utc(raw.occurred_at) for prior in prior_dates if prior is not None):
                        item.source_item_ids = list(dict.fromkeys([*item.source_item_ids, raw.id]))
                        continue
                if candidate["operation"] != "upsert" and not candidate["reference"]:
                    unmatched += 1
                    continue
                if item is None:
                    item = TodayItem(user_id=user_id, event_key=key, kind=candidate["kind"], source_item_ids=[])
                    db.add(item)
                if candidate["operation"] != "upsert":
                    # A dated cancellation can arrive before its older confirmation is scanned.
                    item.status = "cancelled" if candidate["operation"] == "cancel" else "done"
                item.title, item.detail, item.day = candidate["title"], candidate["detail"], candidate["day"]
                item.starts_at = datetime.fromisoformat(candidate["at"]).astimezone(timezone.utc) if candidate["at"] else None
                item.source_item_ids = list(dict.fromkeys([*item.source_item_ids, raw.id]))
                db.flush()
            scan = db.get(TodayScan, raw.id)
            if scan is None:
                scan = TodayScan(raw_item_id=raw.id, user_id=user_id)
                db.add(scan)
            scan.status = "extracted"
            db.commit()
            results.append({"email_id": raw.id, "status": "checked", "items_found": len(candidates),
                            "unmatched_updates": unmatched})
        return {"emails_checked": len(results), "results": results,
                "message": f"Checked {len(results)} imported emails. No automatic retries. Only today's dated items appear."}
    finally:
        _release_email_lease(db, user_id, owner)


def today_items(db: Session, user_id: str, timezone_name: str) -> dict:
    zone = today_zone(timezone_name)
    now = _today_now()
    day = now.astimezone(zone).date().isoformat()
    output = []
    for item in db.scalars(select(TodayItem).where(TodayItem.user_id == user_id, TodayItem.status == "pending")):
        at = _utc(item.starts_at) if item.starts_at else None
        sources = list(db.scalars(select(RawItem).where(
            RawItem.user_id == user_id, RawItem.id.in_(item.source_item_ids),
        ).order_by(RawItem.occurred_at.desc(), RawItem.id)))
        calendar = next((raw for raw in sources if raw.provider == "gcal" and raw.payload_json.get("source") == "gcal_live"),None)
        all_day = bool(calendar and calendar.payload_json.get("all_day"))
        item_day = at.astimezone(zone).date().isoformat() if at else item.day
        if calendar:
            if calendar.payload_json.get("status") != "confirmed":
                continue
            if all_day:
                if not calendar.payload_json["start_date"] <= day < calendar.payload_json["end_date"]:
                    continue
            elif item_day != day or at is None or at <= now:
                continue
        elif item_day != day or (item.kind == "reminder" and (at is None or at <= now)):
            continue
        if item.snoozed_until and _utc(item.snoozed_until) > now:
            continue
        source_url = None
        if calendar:
            source_url = calendar.payload_json.get("html_link")
        elif sources:
            parts = sources[0].external_id.split(":", 2)
            if len(parts) == 3 and parts[0] == "gmail_live":
                source_url = f"https://mail.google.com/mail/u/?authuser={quote(parts[1], safe='')}#all/{quote(parts[2], safe='')}"
        output.append({"id":item.id, "kind":item.kind, "title":item.title, "detail":item.detail,
                       "day":day, "at":at.isoformat() if at else None, "source_url":source_url,
                       "source":"gcal" if calendar else "gmail", "all_day":all_day})
    output.sort(key=lambda item: (item["at"] is None, item["at"] or "", item["title"], item["id"]))
    pending = db.scalar(select(func.count()).select_from(_today_pending_query(user_id).subquery()))
    failed = db.scalar(select(func.count(TodayScan.raw_item_id)).where(
        TodayScan.user_id == user_id, TodayScan.status == "failed",
    ))
    return {"date":day, "timezone":timezone_name, "items":output,
            "action_count":sum(item["kind"] == "action" for item in output),
            "reminder_count":sum(item["kind"] == "reminder" for item in output),
            "pending_emails":pending, "failed_scans":failed}


def process_imported_email(db: Session, user_id: str) -> dict:
    owner = _acquire_email_lease(db, user_id)
    try:
        item = db.execute(select(RawItem).where(
            RawItem.user_id == user_id, RawItem.provider == "gmail",
            RawItem.external_id.startswith("gmail_live:"), RawItem.processed.is_(False),
        ).order_by(RawItem.occurred_at.desc(), RawItem.id).limit(1)).scalar_one_or_none()
        if item is None:
            return {"emails_processed":0, "facts_created":0, "traits_created":0,
                    "message":"No pending imported emails. No model call was made."}
        if item.payload_json.get("source") != "gmail_live":
            raise EmailProcessingError("Imported email source is invalid. No model call was made.")
        observations = extract_one_imported_email(db, user_id, item)
        # Model calls commit their usage before validation; reload after that transaction.
        db.refresh(item)
        if item.processed:
            raise EmailProcessingError("Email was already processed. No duplicate facts were saved.")
        count = 0
        for index, observation in enumerate(observations):
            key = f"email_observation_{item.id}_{index}"
            blocked = db.scalar(select(BlockedTrait.id).where(
                BlockedTrait.user_id == user_id, BlockedTrait.category == observation["category"],
                BlockedTrait.key == key,
            ))
            if blocked:
                continue
            fact = Fact(
                user_id=user_id, category=observation["category"], statement=observation["statement"],
                source_item_ids=[item.id], model_used="nano", privacy_tier="private",
                tier_reason="Imported email observation: private until reviewed by the user.", synthesized=True,
            )
            db.add(fact)
            db.flush()
            db.add(Trait(
                user_id=user_id, category=fact.category, key=key, value=fact.statement,
                confidence=0.3, evidence_count=1, supporting_fact_ids=[fact.id],
                summary=f'Single email observation, not an established preference. Evidence: "{observation["evidence_quote"]}"',
                privacy_tier="private", tier_reason=fact.tier_reason,
                user_overridden=True,
            ))
            count += 1
        item.processed = True
        db.commit()
        return {"emails_processed":1, "facts_created":count, "traits_created":count,
                "message":f"Processed one email with Nano. Created {count} private observation(s). Review in My Profile; change to Ask to use in personalized Chat. No Super or Ultra calls."}
    finally:
        _release_email_lease(db, user_id, owner)


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _event_statement(kind: str, event: dict) -> str:
    if kind == "food_order":
        dishes = list(dict.fromkeys(entry["value"] for entry in event["dishes"]))
        restaurant = event["restaurant"]
        return "Ordered " + ", ".join(dishes) + (f' from {restaurant["value"]}' if restaurant else "") + "."
    if kind == "sports_booking":
        venue = event["venue"]
        return f'Booked {event["sport"]["value"]}' + (f' at {venue["value"]}' if venue else "") + "."
    return f'Booked travel to {event["destination"]["value"]}.'


def _event_key(item: RawItem, kind: str, event: dict) -> str:
    order_id = event.get("order_id")
    merchant = event.get("restaurant") if kind == "food_order" else event.get("venue")
    # Without a merchant-scoped identifier, never guess that two similar orders are duplicates.
    if order_id and merchant:
        mailbox = item.external_id.rsplit(":", 1)[0]
        identity = [mailbox, kind, _normalized(merchant["value"]), order_id["value"].strip()]
    else:
        identity = [item.id]
    return hashlib.sha256(str(identity).encode()).hexdigest()


def process_email_batch(db: Session, user_id: str) -> dict:
    owner = _acquire_email_lease(db, user_id)
    results = []
    try:
        items = list(db.scalars(select(RawItem).where(
            RawItem.user_id == user_id, RawItem.provider == "gmail",
            RawItem.external_id.startswith("gmail_live:"), RawItem.processed.is_(False),
        ).order_by(RawItem.occurred_at.desc(), RawItem.id).limit(5)))
        if any((item.payload_json or {}).get("source") != "gmail_live" for item in items):
            raise EmailProcessingError("Invalid live email source; no calls made.")
        unclassified = [item for item in items if db.get(EmailIngestion, item.id) is None]
        if unclassified:
            labels = classify_imported_emails(db, user_id, unclassified)
            for item in unclassified:
                db.add(EmailIngestion(raw_item_id=item.id, user_id=user_id, kind=labels[item.id]))
            db.commit()
        for item in items:
            state = db.get(EmailIngestion, item.id)
            if state is None:
                raise EmailProcessingError("Email classification missing; no extraction attempted.")
            if state.kind not in EVENT_CATEGORIES:
                state.status = "review" if state.kind == "unknown" else "skipped"
                item.processed = True
                db.commit()
                results.append({"email_id": item.id, "type": state.kind, "status": state.status})
                continue
            try:
                event = extract_email_event(db, user_id, item, state.kind)
            except (EmailProcessingError, BudgetExceededError) as exc:
                db.rollback()
                state = db.get(EmailIngestion, item.id)
                state.status = "failed"
                db.commit()
                results.append({"email_id": item.id, "type": state.kind, "status": "failed", "error": str(exc)})
                if isinstance(exc, BudgetExceededError):
                    break
                continue
            db.refresh(item)
            lease = db.get(EmailProcessingLease, user_id)
            if item.processed or lease is None or lease.owner != owner or lease.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
                raise EmailProcessingError("Processing ownership changed; no duplicate event saved. Check usage.")
            state.event_key = _event_key(item, state.kind, event)
            removed = db.scalar(select(EmailIngestion.raw_item_id).where(
                EmailIngestion.user_id == user_id, EmailIngestion.event_key == state.event_key,
                EmailIngestion.status == "removed",
            ))
            if removed:
                state.status, state.event, state.fact_id = "removed", None, None
                item.processed = True
                db.commit()
                results.append({"email_id": item.id, "type": state.kind, "status": "excluded"})
                continue
            duplicate = db.scalar(select(EmailIngestion).where(
                EmailIngestion.user_id == user_id, EmailIngestion.event_key == state.event_key,
                EmailIngestion.status == "extracted", EmailIngestion.raw_item_id != item.id,
            ))
            fact = db.get(Fact, duplicate.fact_id) if duplicate and duplicate.fact_id else None
            if fact:
                if state.kind == "food_order" and duplicate.event:
                    dishes = {_normalized(entry["value"]): entry for entry in duplicate.event["dishes"]}
                    for entry in event["dishes"]:
                        dishes.setdefault(_normalized(entry["value"]), entry)
                    concepts = {entry["dish"]: entry for entry in duplicate.event.get("concepts", [])}
                    concepts.update({entry["dish"]: entry for entry in event.get("concepts", [])})
                    event = {**duplicate.event, "dishes": list(dishes.values()), "concepts": list(concepts.values())}
                elif duplicate.event:
                    event = duplicate.event
                fact.source_item_ids = list(dict.fromkeys([*fact.source_item_ids, item.id]))
                fact.statement = _event_statement(state.kind, event)
                for receipt in db.scalars(select(EmailIngestion).where(
                    EmailIngestion.user_id == user_id, EmailIngestion.event_key == state.event_key,
                    EmailIngestion.status == "extracted",
                )):
                    receipt.event = event
            else:
                fact = Fact(user_id=user_id, category=EVENT_CATEGORIES[state.kind],
                            statement=_event_statement(state.kind, event), source_item_ids=[item.id],
                            model_used="super", privacy_tier="private", synthesized=True,
                            tier_reason="Structured activity evidence; private by default.")
                db.add(fact)
                db.flush()
            state.fact_id, state.event, state.status = fact.id, event, "extracted"
            item.processed = True
            db.flush()
            refresh_activity_traits(db, user_id)
            db.commit()
            results.append({"email_id": item.id, "type": state.kind, "status": "duplicate" if duplicate else "extracted"})
        refresh_activity_traits(db, user_id)
        db.commit()
        return {"results": results, "emails_processed": sum(r["status"] != "failed" for r in results),
                "message": f'Checked {len(results)} emails. {sum(r["status"] == "failed" for r in results)} failed; unknown emails await review. No automatic retries.'}
    finally:
        _release_email_lease(db, user_id, owner)


def activity_rankings(db: Session, user_id: str) -> dict:
    groups: dict[str, dict] = {}
    seen = set()
    for row in db.scalars(select(EmailIngestion).where(
        EmailIngestion.user_id == user_id, EmailIngestion.status == "extracted"
    ).order_by(EmailIngestion.raw_item_id)):
        if not row.event or row.event_key in seen or not row.fact_id:
            continue
        seen.add(row.event_key)
        event = row.event
        if row.kind == "food_order":
            dimensions = [("foods", entry["value"], None) for entry in event["dishes"]]
            if event["restaurant"]:
                dimensions += [("restaurants", event["restaurant"]["value"], entry["value"]) for entry in event["dishes"]]
        elif row.kind == "sports_booking":
            dimensions = [("sports", event["sport"]["value"], None)]
            if event["venue"]:
                dimensions.append(("venues", event["venue"]["value"], event["sport"]["value"]))
        else:
            dimensions = [("destinations", event["destination"]["value"], None)]
        for dimension, name, child in dimensions:
            group = groups.setdefault(dimension, {})
            entry = group.setdefault(_normalized(name), {"name": name, "facts": set(), "children": {}})
            entry["facts"].add(row.fact_id)
            if child:
                sub = entry["children"].setdefault(_normalized(child), {"name": child, "facts": set()})
                sub["facts"].add(row.fact_id)
    output = {}
    for dimension, group in groups.items():
        output[dimension] = sorted([
            {"name": entry["name"], "count": len(entry["facts"]), "fact_ids": sorted(entry["facts"]),
             "children": sorted([{"name": sub["name"], "count": len(sub["facts"])} for sub in entry["children"].values()],
                                key=lambda sub: (-sub["count"], _normalized(sub["name"])))}
            for entry in group.values()
        ], key=lambda entry: (-entry["count"], _normalized(entry["name"])))
    return output


ACTIVITY_DIMENSIONS = {
    "foods": ("food", "Most ordered foods"),
    "restaurants": ("food", "Most ordered restaurants"),
    "sports": ("fitness", "Most booked sports"),
    "venues": ("fitness", "Frequently booked sports venues"),
    "destinations": ("places_travel", "Booked destinations"),
}


def _preference_groups(db: Session, user_id: str) -> tuple[dict, dict, dict]:
    foods: dict[str, dict] = {}
    sports: dict[str, dict] = {}
    totals: dict[str, set] = {"food": set(), "fitness": set()}
    seen = set()

    def add(groups, key, label, fact_id, example=None, restaurant=None):
        group = groups.setdefault(key, {"label": label, "facts": set(), "examples": {}, "restaurants": {}})
        group["facts"].add(fact_id)
        if example:
            group["examples"].setdefault(example, set()).add(fact_id)
        if restaurant:
            normalized = _normalized(restaurant)
            entry = group["restaurants"].setdefault(normalized, {"name": restaurant.strip(), "facts": set()})
            entry["facts"].add(fact_id)

    for row in db.scalars(select(EmailIngestion).where(
        EmailIngestion.user_id == user_id, EmailIngestion.status == "extracted",
    ).order_by(EmailIngestion.raw_item_id)):
        if not row.event or not row.fact_id or row.fact_id in seen:
            continue
        seen.add(row.fact_id)
        event = row.event
        if row.kind == "food_order":
            totals["food"].add(row.fact_id)
            concepts = {entry["dish"]: entry for entry in event.get("concepts", [])}
            for dish in event["dishes"]:
                group, family = food_concept(dish["value"])
                concept = concepts.get(dish["value"])
                # Recognizable historical dish names need no paid backfill. Model interpretations
                # supply the missing context for unfamiliar dishes, never replace exact evidence.
                if group == "other" and concept:
                    group, family = concept["group"], concept["family"]
                if family:
                    family = _normalized(family)
                restaurant = event.get("restaurant")
                add(foods, group, FOOD_GROUP_LABELS[group], row.fact_id, family,
                    restaurant["value"] if restaurant else None)
        elif row.kind == "sports_booking":
            totals["fitness"].add(row.fact_id)
            name = _normalized(event["sport"]["value"])
            name = {"box cricket": "cricket", "futsal": "football", "soccer": "football",
                    "table-tennis": "table tennis"}.get(name, name)
            key = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
            if not key:
                key = hashlib.sha256(name.encode()).hexdigest()[:12]
            venue = event.get("venue")
            add(sports, key, name, row.fact_id, restaurant=venue["value"] if venue else None)
    return foods, sports, totals


def _frequency(group: dict, groups: dict, total: int, verb: str) -> str:
    count = len(group["facts"])
    maximum = max(len(entry["facts"]) for entry in groups.values())
    leaders = sum(len(entry["facts"]) == maximum for entry in groups.values())
    if count == 1:
        return "Has ordered" if verb == "orders" else "Has booked"
    if count == maximum and leaders == 1:
        return "Most often " + verb
    if count / total >= 0.2:
        return "Also " + verb
    return "Occasionally " + verb


def _examples(group: dict) -> str:
    examples = sorted(group["examples"], key=lambda name: (-len(group["examples"][name]), name))
    if not examples:
        return ""
    top = examples[0]
    if len(examples) == 1:
        return "" if top.casefold() == group["label"].casefold() else ", especially " + top
    if len(group["examples"][top]) > len(group["examples"][examples[1]]):
        secondary = examples[1:3]
        occasional = all(len(group["examples"][name]) <= len(group["facts"]) / 3 for name in secondary)
        return ", especially " + top + ("; occasionally also tries " if occasional else "; also orders ") + " and ".join(secondary)
    return ", including " + " and ".join(examples[:3])


def _preference_specs(db: Session, user_id: str) -> dict:
    foods, sports, totals = _preference_groups(db, user_id)
    specs = {}
    restaurant_groups = {
        key: {"facts": set().union(*(entry["facts"] for entry in group["restaurants"].values()))}
        for key, group in foods.items() if group["restaurants"]
    }
    restaurant_total = len(set().union(*(group["facts"] for group in restaurant_groups.values())))
    for key, group in foods.items():
        if key == "other":
            continue
        prefix = _frequency(group, foods, len(totals["food"]), "orders")
        specs["preference_food_" + key] = (
            "food", "foods", prefix + " " + group["label"] + _examples(group) + ".", group["facts"],
        )
        restaurants = sorted(group["restaurants"].values(),
                             key=lambda entry: (-len(entry["facts"]), _normalized(entry["name"])))
        if restaurants:
            names = _place_examples(restaurants)
            restaurant_facts = restaurant_groups[key]["facts"]
            restaurant_prefix = _frequency(restaurant_groups[key], restaurant_groups, restaurant_total, "orders")
            specs["preference_restaurants_" + key] = (
                "food", "restaurants",
                restaurant_prefix + " " + group["label"] + " from places such as " + names + ".",
                restaurant_facts,
            )
    for key, group in sports.items():
        prefix = _frequency(group, sports, len(totals["fitness"]), "books")
        venues = sorted(group["restaurants"].values(),
                        key=lambda entry: (-len(entry["facts"]), _normalized(entry["name"])))
        venue_text = ""
        if venues:
            venue_text = ", at " + _place_examples(venues, " and ")
            if len(venues) > 1 and len(venues[0]["facts"]) > len(venues[1]["facts"]):
                venue_text = ", usually at " + venues[0]["name"]
        specs["preference_sport_" + key] = (
            "fitness", "sports", prefix + " " + group["label"] + venue_text + ".", group["facts"],
        )
    return specs


def _place_examples(entries: list[dict], separator: str = ", ") -> str:
    names: list[str] = []
    for entry in entries[:3]:
        if names and len(separator.join([*names, entry["name"]])) > 160:
            break
        names.append(entry["name"])
    return separator.join(names)


def refresh_activity_traits(db: Session, user_id: str) -> None:
    specs = _preference_specs(db, user_id)
    traits = list(db.scalars(select(Trait).where(Trait.user_id == user_id)))
    blocked = set(db.execute(select(BlockedTrait.category, BlockedTrait.key).where(
        BlockedTrait.user_id == user_id,
    )).all())
    frozen = {trait.key for trait in traits if trait.user_overridden}
    for trait in traits:
        if trait.key.startswith("preference_") and trait.key not in specs and not trait.user_overridden:
            db.delete(trait)
        elif trait.key in ("activity_foods", "activity_restaurants", "activity_sports", "activity_venues"):
            if not trait.user_overridden:
                db.delete(trait)
    for key, (category, dimension, value, facts) in specs.items():
        legacy = "activity_" + dimension
        if ((category, key) in blocked or (category, legacy) in blocked
                or legacy in frozen or (dimension == "sports" and (
                    "activity_venues" in frozen or ("fitness", "activity_venues") in blocked))):
            continue
        trait = next((trait for trait in traits if trait.key == key), None)
        if trait and trait.user_overridden:
            continue
        if trait is None:
            trait = Trait(user_id=user_id, category=category, key=key)
            db.add(trait)
        trait.value = value
        trait.supporting_fact_ids, trait.evidence_count = sorted(facts), len(facts)
        activity = "order" if category == "food" else "booking"
        trait.summary = (
            f"Based on {len(facts)} distinct imported {activity}{'s' if len(facts) != 1 else ''}. "
            "An observed pattern, not confirmed likes or attendance. Exact items and counts are in Activity details."
        )
        trait.confidence = round(min(0.85, 0.3 + 0.04 * (len(facts) - 1)), 2)
        trait.privacy_tier = "private"
        trait.tier_reason = "Evidence-backed preference interpretation. Editing or changing privacy freezes this snapshot."
    # Travel retains its existing compact summary; food and sport cards are reconciled together above.
    rankings = activity_rankings(db, user_id)
    for dimension, (category, label) in ACTIVITY_DIMENSIONS.items():
        if dimension != "destinations":
            continue
        entries = rankings.get(dimension, [])
        key = "activity_" + dimension
        trait = db.scalar(select(Trait).where(Trait.user_id == user_id, Trait.category == category, Trait.key == key))
        if not entries:
            if trait and not trait.user_overridden:
                db.delete(trait)
            continue
        if db.scalar(select(BlockedTrait.id).where(
            BlockedTrait.user_id == user_id, BlockedTrait.category == category, BlockedTrait.key == key,
        )):
            continue
        if trait and trait.user_overridden:
            continue
        facts = sorted({fid for entry in entries[:5] for fid in entry["fact_ids"]})
        value = label + ": " + "; ".join(f'{entry["name"]} ({entry["count"]})' for entry in entries[:5])
        summary = "All imported activity, not confirmed likes or attendance. Top 5 shown; expand Activity details for all."
        if trait is None:
            trait = Trait(user_id=user_id, category=category, key=key)
            db.add(trait)
        trait.value, trait.summary = value, summary
        trait.supporting_fact_ids, trait.evidence_count = facts, len(facts)
        trait.confidence = 0.3
        trait.privacy_tier = "private"
        trait.tier_reason = "Aggregated activity; review before sharing. Editing or changing privacy freezes this snapshot."


# ---------------------------------------------------------------------------
# Demo-user bootstrapping
# ---------------------------------------------------------------------------
# Personal Nemo is scoped to a single demo user per plan.md (no multi-user auth
# flow is specified). `get_or_create_demo_user` makes sure that user row always
# exists so every route can depend on a stable user_id.


def get_or_create_demo_user(db: Session) -> User:
    settings = get_settings()
    user = db.execute(select(User).where(User.email == settings.demo_user_email)).scalar_one_or_none()
    if user is None:
        user = User(email=settings.demo_user_email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Explicit life context (never exposed through MCP trait retrieval)
# ---------------------------------------------------------------------------


class HouseholdMember(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    relationship: str = Field(min_length=1, max_length=80)
    likes: str = Field(default="", max_length=300)
    dislikes: str = Field(default="", max_length=300)
    dietary_needs: str = Field(default="", max_length=300)


class MyLife(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    home_city: str = Field(default="", max_length=120)
    home_area: str = Field(default="", max_length=120)
    office_city: str = Field(default="", max_length=120)
    office_area: str = Field(default="", max_length=120)
    work_arrangement: str = Field(default="", max_length=300)
    household_members: list[HouseholdMember] = Field(default_factory=list, max_length=10)


def get_my_life(db: Session, user_id: str) -> MyLife:
    context = db.get(LifeContext, user_id)
    return MyLife.model_validate(context.data) if context else MyLife()


def save_my_life(db: Session, user_id: str, data: MyLife) -> MyLife:
    context = db.get(LifeContext, user_id)
    if context is None:
        context = LifeContext(user_id=user_id)
        db.add(context)
    context.data = data.model_dump()
    db.commit()
    return data


def life_context_block(db: Session, user_id: str) -> str:
    return get_my_life(db, user_id).model_dump_json()


def approval_snapshot(traits: list[Trait]) -> list[dict]:
    return [
        {"id": trait.id, "category": trait.category, "value": trait.value, "summary": trait.summary}
        for trait in sorted(traits, key=lambda trait: trait.id)
    ]


def refresh_approval(db: Session, request: MCPApproval) -> None:
    """Invalidate grants after expiry, token rotation, edits, deletion, or tier changes."""
    if request.status not in ("pending", "approved"):
        return
    expires = request.expires_at.replace(tzinfo=timezone.utc)
    owner = db.get(User, request.user_id)
    traits = list(db.execute(select(Trait).where(
        Trait.user_id == request.user_id,
        Trait.id.in_([item["id"] for item in request.snapshot]),
        Trait.privacy_tier == "ask",
    )).scalars())
    status = None
    if expires <= datetime.now(timezone.utc):
        status = "expired"
    elif owner is None or owner.mcp_token_hash != request.token_hash or approval_snapshot(traits) != request.snapshot:
        status = "invalidated"
    if status:
        db.execute(update(MCPApproval).where(
            MCPApproval.id == request.id, MCPApproval.status.in_(("pending", "approved"))
        ).values(status=status))
        db.commit()
        db.refresh(request)


# ---------------------------------------------------------------------------
# Relevant-trait retrieval for Chat and the MCP server (plan.md section 8D / 9)
# ---------------------------------------------------------------------------
# No embedding model is wired up yet (Token Factory embedding availability is
# unconfirmed per plan.md section 3), so retrieval falls back to category +
# keyword matching, with a top-confidence fallback when nothing matches.

_KEYWORD_TO_CATEGORIES: dict[str, list[str]] = {
    "food": [Category.FOOD], "eat": [Category.FOOD], "restaurant": [Category.FOOD], "cuisine": [Category.FOOD],
    "dinner": [Category.FOOD], "lunch": [Category.FOOD], "order": [Category.FOOD],
    "music": [Category.MUSIC], "song": [Category.MUSIC], "artist": [Category.MUSIC], "playlist": [Category.MUSIC],
    "movie": [Category.ENTERTAINMENT], "show": [Category.ENTERTAINMENT], "watch": [Category.ENTERTAINMENT],
    "series": [Category.ENTERTAINMENT], "film": [Category.ENTERTAINMENT],
    "gym": [Category.FITNESS], "workout": [Category.FITNESS], "run": [Category.FITNESS], "fitness": [Category.FITNESS],
    "exercise": [Category.FITNESS],
    "sport": [Category.FITNESS], "badminton": [Category.FITNESS], "tennis": [Category.FITNESS],
    "schedule": [Category.ROUTINE], "routine": [Category.ROUTINE], "morning": [Category.ROUTINE], "day": [Category.ROUTINE],
    "trip": [Category.PLACES_TRAVEL], "travel": [Category.PLACES_TRAVEL], "vacation": [Category.PLACES_TRAVEL],
    "place": [Category.PLACES_TRAVEL],
    "outfit": [Category.STYLE_SHOPPING], "clothes": [Category.STYLE_SHOPPING], "wear": [Category.STYLE_SHOPPING],
    "fashion": [Category.STYLE_SHOPPING], "shoe": [Category.STYLE_SHOPPING], "sneaker": [Category.STYLE_SHOPPING],
    "personality": [Category.PERSONALITY], "character": [Category.PERSONALITY],
}


def categories_for_query(query_text: str) -> list[str]:
    lowered = query_text.lower()
    matched: list[str] = []
    for keyword, categories in _KEYWORD_TO_CATEGORIES.items():
        if keyword in lowered:
            for cat in categories:
                if cat not in matched:
                    matched.append(cat)
    return matched


def retrieve_relevant_traits(
    db: Session,
    user_id: str,
    query_text: str,
    *,
    include_tiers: tuple[str, ...] = ("share", "ask"),
    max_traits: int = 8,
) -> list[Trait]:
    """Returns the traits most relevant to free-text query_text, respecting tiers.

    Falls back to the user's highest-confidence traits across all categories
    when no keyword matches, so chat/MCP answers still have something to use.
    """
    base_query = select(Trait).where(Trait.user_id == user_id, Trait.privacy_tier.in_(include_tiers))
    matched_categories = categories_for_query(query_text)

    if matched_categories:
        traits = list(
            db.execute(
                base_query.where(Trait.category.in_(matched_categories)).order_by(Trait.confidence.desc())
            ).scalars()
        )
        if traits:
            return traits[:max_traits]

    traits = list(db.execute(base_query.order_by(Trait.confidence.desc())).scalars())
    return traits[:max_traits]


# ---------------------------------------------------------------------------
# Demo data loading (plan.md section 6 "Fallback rule" and section 7
# POST /api/demo/load): loads every connector's bundled sample data into
# raw_items, marks each connection as "demo", then runs the full Nemotron
# pipeline so the profile is populated end to end with zero OAuth required.
# ---------------------------------------------------------------------------


def _get_or_create_connection(db: Session, user_id: str, provider: str) -> Connection:
    connection = db.execute(
        select(Connection).where(Connection.user_id == user_id, Connection.provider == provider)
    ).scalar_one_or_none()
    if connection is None:
        connection = Connection(user_id=user_id, provider=provider)
        db.add(connection)
    return connection


def load_sample_data_for_provider(db: Session, user_id: str, provider: str) -> int:
    """Loads one provider's sample data as raw_items (idempotent on external_id).

    Returns the number of newly inserted raw_items.
    """
    connector = get_connector(provider)
    items = connector.load_sample_data()

    existing_external_ids = {
        row[0]
        for row in db.execute(
            select(RawItem.external_id).where(RawItem.user_id == user_id, RawItem.provider == provider)
        ).all()
    }

    inserted = 0
    for item in items:
        if item.external_id in existing_external_ids:
            continue
        db.add(
            RawItem(
                user_id=user_id,
                provider=provider,
                external_id=item.external_id,
                item_type=item.item_type,
                payload_json=item.payload,
                occurred_at=item.occurred_at,
                processed=False,
            )
        )
        inserted += 1

    connection = _get_or_create_connection(db, user_id, provider)
    if not connection.encrypted_refresh_token:
        connection.status = "demo"
        connection.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    return inserted


def run_full_pipeline_for_user(db: Session, user_id: str) -> dict:
    """Extraction -> privacy tagging -> synthesis, in order, for all new data.

    This is the *real*, paid pipeline (Nano/Super/Ultra calls). It is not
    called by the demo-load path (see `seed_demo_profile_for_user` below) but
    stays available - e.g. the Profile page's "Rebuild profile" button calls
    the privacy-tagging and synthesis stages directly on whatever facts exist,
    including the zero-cost seeded ones below, to genuinely re-run them
    through a real provider once one is configured.
    """
    facts_created = run_extraction_for_user(db, user_id)
    facts_tagged = run_privacy_tagging_for_user(db, user_id)
    traits_upserted = run_synthesis_for_user(db, user_id)
    return {
        "facts_created": facts_created,
        "facts_tagged": facts_tagged,
        "traits_upserted": traits_upserted,
    }


def seed_demo_profile_for_user(db: Session, user_id: str) -> dict:
    """Instantly populates a curated, evidence-linked demo profile with ZERO
    LLM calls (Plan B: instant demo mode).

    Facts are inserted with `privacy_tier=None` - exactly like real,
    freshly-extracted facts before the Super privacy tagger has ever run -
    so that a later real "Rebuild profile" call (once a funded provider key
    is configured) genuinely re-tags and re-synthesizes these same facts,
    naturally replacing the seeded placeholder trait values with real AI
    output. Traits are inserted fully populated so the profile and Know Me
    Better screens look complete immediately.

    Idempotent: safe to call repeatedly (e.g. re-clicking "Load sample data")
    without creating duplicate facts, and without clobbering traits the user
    has since edited (`user_overridden`) or deleted (`BlockedTrait`).
    """
    external_id_to_raw_id: dict[str, str] = {
        row.external_id: row.id
        for row in db.execute(select(RawItem.external_id, RawItem.id).where(RawItem.user_id == user_id)).all()
    }

    existing_fact_ids_by_statement: dict[str, str] = {
        row.statement: row.id
        for row in db.execute(select(Fact.statement, Fact.id).where(Fact.user_id == user_id)).all()
    }

    fact_key_to_id: dict[str, str] = {}
    facts_created = 0
    for seed_fact in DEMO_SEED_FACTS:
        statement = seed_fact["statement"]
        existing_id = existing_fact_ids_by_statement.get(statement)
        if existing_id is not None:
            fact_key_to_id[seed_fact["fact_key"]] = existing_id
            continue

        source_ids = [
            external_id_to_raw_id[ext_id]
            for ext_id in seed_fact["source_external_ids"]
            if ext_id in external_id_to_raw_id
        ]
        fact = Fact(
            user_id=user_id,
            category=seed_fact["category"],
            statement=statement,
            source_item_ids=source_ids,
            model_used="demo_seed",
            privacy_tier=None,  # untagged - a real Rebuild profile call will tag it for real later
        )
        db.add(fact)
        db.flush()  # assigns fact.id without a full commit, so trait linking below can use it
        fact_key_to_id[seed_fact["fact_key"]] = fact.id
        facts_created += 1
    db.commit()

    blocked_keys = {
        (row.category, row.key)
        for row in db.execute(
            select(BlockedTrait.category, BlockedTrait.key).where(BlockedTrait.user_id == user_id)
        ).all()
    }
    existing_traits = {
        (t.category, t.key): t
        for t in db.execute(select(Trait).where(Trait.user_id == user_id)).scalars()
    }

    traits_upserted = 0
    for seed_trait in DEMO_SEED_TRAITS:
        category, key = seed_trait["category"], seed_trait["key"]
        if (category, key) in blocked_keys and (category, key) not in existing_traits:
            continue  # user explicitly deleted this trait before - don't recreate it

        supporting_fact_ids = [fact_key_to_id[fk] for fk in seed_trait["fact_keys"] if fk in fact_key_to_id]

        trait = existing_traits.get((category, key))
        if trait is None:
            trait = Trait(user_id=user_id, category=category, key=key)
            db.add(trait)
            existing_traits[(category, key)] = trait

        if not trait.user_overridden:
            trait.value = seed_trait["value"]
            trait.summary = seed_trait["summary"]
            trait.confidence = seed_trait["confidence"]
            trait.evidence_count = len(supporting_fact_ids)
            trait.supporting_fact_ids = supporting_fact_ids
            trait.privacy_tier = DEFAULT_CATEGORY_TIER.get(category, "ask")
            trait.tier_reason = "Category default for seeded demo data - run Rebuild profile with a real AI key for a per-fact privacy review."
        traits_upserted += 1
    db.commit()

    return {"facts_created": facts_created, "traits_upserted": traits_upserted}


def load_all_demo_data(db: Session, user_id: str) -> dict:
    """Loads sample raw_items for every provider, then instantly seeds a
    curated demo profile (zero LLM cost) - see `seed_demo_profile_for_user`.
    """
    inserted_by_provider = {provider: load_sample_data_for_provider(db, user_id, provider) for provider in all_providers()}
    seed_result = seed_demo_profile_for_user(db, user_id)
    return {"inserted_by_provider": inserted_by_provider, **seed_result}