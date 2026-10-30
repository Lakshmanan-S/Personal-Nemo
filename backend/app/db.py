"""
Database engine/session setup and the full data model (plan.md section 5), in
one module: users, connections, raw_items, facts, traits, qa_sessions/answers,
chat_messages, model_calls, mcp_access_log, plus a few supporting tables
(blocked_traits, category_privacy_settings, daily_nudges).
"""
import uuid
from collections.abc import Generator
from datetime import datetime, timezone


from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


from app.config import get_settings


settings = get_settings()


connect_args = {"check_same_thread": False} if settings.effective_database_url.startswith("sqlite") else {}
engine = create_engine(settings.effective_database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)




class Base(DeclarativeBase):
    pass




def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()




def init_db() -> None:
    """Create all tables. SQLite + create_all is sufficient for the hackathon scope."""
    Base.metadata.create_all(bind=engine)




def _uuid() -> str:
    return str(uuid.uuid4())




def _now() -> datetime:
    return datetime.now(timezone.utc)




class User(Base):
    __tablename__ = "users"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    mcp_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)


    connections: Mapped[list["Connection"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    raw_items: Mapped[list["RawItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    facts: Mapped[list["Fact"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    traits: Mapped[list["Trait"]] = relationship(back_populates="user", cascade="all, delete-orphan")




class Connection(Base):
    """One OAuth/data-source connection per (user, provider)."""


    __tablename__ = "connections"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String, index=True)  # gmail, gcal, spotify, strava, trakt
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="disconnected")  # disconnected, connected, demo, error


    user: Mapped["User"] = relationship(back_populates="connections")




class LifeContext(Base):
    """Explicit home/work/household context, isolated from MCP-shareable traits."""


    __tablename__ = "life_contexts"


    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)




class OAuthState(Base):
    __tablename__ = "oauth_states"


    state_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    browser_hash: Mapped[str] = mapped_column(String)
    encrypted_verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))




class GmailAccount(Base):
    __tablename__ = "gmail_accounts"


    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    email: Mapped[str] = mapped_column(String)




class CalendarAccount(Base):
    __tablename__ = "calendar_accounts"


    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String)




class CalendarLearning(Base):
    __tablename__ = "calendar_learning"


    raw_item_id: Mapped[str] = mapped_column(ForeignKey("raw_items.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_hash: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    candidate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fact_id: Mapped[str | None] = mapped_column(String, nullable=True)




class EmailProcessingLease(Base):
    __tablename__ = "email_processing_leases"


    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))




class EmailIngestion(Base):
    __tablename__ = "email_ingestions"


    raw_item_id: Mapped[str] = mapped_column(ForeignKey("raw_items.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="classified")
    event: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    event_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    fact_id: Mapped[str | None] = mapped_column(String, nullable=True)




class TodayScan(Base):
    __tablename__ = "today_scans"


    raw_item_id: Mapped[str] = mapped_column(ForeignKey("raw_items.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    candidate: Mapped[dict | None] = mapped_column(JSON, nullable=True)




class TodayItem(Base):
    __tablename__ = "today_items"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    event_key: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    day: Mapped[str] = mapped_column(String, index=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_item_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)




class RawItem(Base):
    """Normalized raw data pulled from a connector (or loaded from sample_data)."""


    __tablename__ = "raw_items"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String, index=True)
    external_id: Mapped[str] = mapped_column(String, index=True)
    item_type: Mapped[str] = mapped_column(String)  # email_receipt, event, track, activity, watch
    payload_json: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


    user: Mapped["User"] = relationship(back_populates="raw_items")




class Fact(Base):
    """Candidate fact extracted by the Nano extraction pipeline."""


    __tablename__ = "facts"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String, index=True)
    statement: Mapped[str] = mapped_column(Text)
    source_item_ids: Mapped[list] = mapped_column(JSON, default=list)
    model_used: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


    # Privacy tagging (Super) result, nullable until tagged.
    privacy_tier: Mapped[str | None] = mapped_column(String, nullable=True)  # share, ask, private
    tier_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


    # Set true once this fact has been folded into trait synthesis.
    synthesized: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


    user: Mapped["User"] = relationship(back_populates="facts")




class Trait(Base):
    """Synthesized (Ultra) trait shown in My Profile."""


    __tablename__ = "traits"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String, index=True)
    key: Mapped[str] = mapped_column(String)  # e.g. "favorite_cuisine"
    value: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_fact_ids: Mapped[list] = mapped_column(JSON, default=list)
    privacy_tier: Mapped[str] = mapped_column(String, default="ask")  # share, ask, private
    tier_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


    user: Mapped["User"] = relationship(back_populates="traits")




class QASession(Base):
    __tablename__ = "qa_sessions"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    topic: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="in_progress")  # in_progress, done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


    answers: Mapped[list["QAAnswer"]] = relationship(back_populates="session", cascade="all, delete-orphan")




class QAAnswer(Base):
    __tablename__ = "qa_answers"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("qa_sessions.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


    session: Mapped["QASession"] = relationship(back_populates="answers")




class ChatMessage(Base):
    __tablename__ = "chat_messages"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String)  # user, assistant
    content: Mapped[str] = mapped_column(Text)
    used_trait_ids: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String, default="personalized")  # personalized, baseline
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)




class ModelCall(Base):
    """Every Nemotron call, for cost control and the Under the Hood panel."""


    __tablename__ = "model_calls"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    task: Mapped[str] = mapped_column(String)  # extraction, privacy_tagging, synthesis, chat, qa, nudge
    model: Mapped[str] = mapped_column(String)  # nano, super, ultra (logical size)
    model_id: Mapped[str] = mapped_column(String)  # actual Nebius model id used
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)




class BlockedTrait(Base):
    """A (category, key) a user explicitly deleted - synthesis must not recreate it."""


    __tablename__ = "blocked_traits"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String)
    key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)




class CategoryPrivacySetting(Base):
    """User-set default privacy tier for a whole category (plan.md PUT /api/privacy/category/{category})."""


    __tablename__ = "category_privacy_settings"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String)
    tier: Mapped[str] = mapped_column(String)  # share, ask, private
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)




class DailyNudge(Base):
    """One cached nudge per user per day, so /api/nudge doesn't re-call Nemotron on every refresh."""


    __tablename__ = "daily_nudges"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    nudge_date: Mapped[str] = mapped_column(String, index=True)  # ISO date, UTC
    nudge_type: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)




class MCPAccessLog(Base):
    __tablename__ = "mcp_access_log"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tool: Mapped[str] = mapped_column(String)
    traits_returned: Mapped[int] = mapped_column(Integer, default=0)
    traits_blocked: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)




class MCPApproval(Base):
    __tablename__ = "mcp_approvals"


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String)
    receipt_hash: Mapped[str] = mapped_column(String)
    client_name: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String)
    snapshot: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))



