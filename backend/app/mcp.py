"""
MCP server in one module (plan.md section 9): the bearer-auth contextvar,
the auth middleware wrapping FastMCP's ASGI app, and the privacy-gated tools.
Share-tier traits are returned directly; Ask traits require an expiring,
one-time approval. Private traits are never released.


Run standalone with: python -m app.mcp
Exposes the streamable-HTTP MCP endpoint (default: http://localhost:8800/mcp),
protected by the bearer token generated via POST /api/mcp/token on the main API.
"""
import hashlib
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone


import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


from app.config import get_settings
from app.db import MCPAccessLog, MCPApproval, SessionLocal, Trait, User, init_db
from app.config import Category
from app.services import approval_snapshot, categories_for_query, refresh_approval, retrieve_relevant_traits


# ---------------------------------------------------------------------------
# Bearer-token auth
# ---------------------------------------------------------------------------


current_mcp_user_id: ContextVar[str | None] = ContextVar("current_mcp_user_id", default=None)
current_mcp_token_hash: ContextVar[str | None] = ContextVar("current_mcp_token_hash", default=None)




class BearerAuthMiddleware:
    """Wraps the FastMCP ASGI app: every HTTP request must carry
    `Authorization: Bearer <token>` matching a user's mcp_token_hash (generated
    via POST /api/mcp/token). The resolved user id is stashed in
    `current_mcp_user_id` so the tool functions below know whose data to serve.
    Non-HTTP scopes (e.g. "lifespan") are passed through untouched so FastMCP's
    session manager lifecycle still works when this app is run directly as the
    ASGI root.
    """


    def __init__(self, app: ASGIApp) -> None:
        self.app = app


    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return


        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode()
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None


        if not token:
            await JSONResponse({"error": "Missing bearer token"}, status_code=401)(scope, receive, send)
            return


        token_hash = hashlib.sha256(token.encode()).hexdigest()
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.mcp_token_hash == token_hash).one_or_none()
        finally:
            db.close()


        if user is None:
            await JSONResponse({"error": "Invalid bearer token"}, status_code=401)(scope, receive, send)
            return


        reset_token = current_mcp_user_id.set(user.id)
        reset_hash = current_mcp_token_hash.set(token_hash)
        try:
            await self.app(scope, receive, send)
        finally:
            current_mcp_user_id.reset(reset_token)
            current_mcp_token_hash.reset(reset_hash)




# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


mcp_app = FastMCP(
    name="personal-nemo",
    instructions=(
        "Access this user's approved personal preferences (food, music, entertainment, "
        "fitness, style and more) to give tailored recommendations. Only traits the user "
        "has marked shareable are returned directly. For ask-tier traits call "
        "request_private_info, then check_approval after the user approves in Personal Nemo. "
        "Keep the returned receipt secret. Private-tier traits are never released. "
        "Pending is not permission; do not wait indefinitely or poll rapidly."
    ),
)




def _client_name(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    try:
        client_info = ctx.request_context.session.client_params.clientInfo
        return client_info.name if client_info else None
    except Exception:
        return None




def _require_user_id() -> str:
    user_id = current_mcp_user_id.get()
    if user_id is None:
        raise PermissionError("No authenticated Personal Nemo user for this MCP session.")
    return user_id




def _log_access(db: Session, user_id: str, tool: str, client_name: str | None, returned: int, blocked: int) -> None:
    db.add(
        MCPAccessLog(
            user_id=user_id,
            client_name=client_name,
            tool=tool,
            traits_returned=returned,
            traits_blocked=blocked,
        )
    )
    db.commit()




@mcp_app.tool()
def get_profile_summary(categories: list[str] | None = None, ctx: Context | None = None) -> str:
    """Short summary of this user's shareable traits, optionally filtered to specific categories."""
    user_id = _require_user_id()
    db = SessionLocal()
    try:
        query = select(Trait).where(Trait.user_id == user_id)
        if categories:
            query = query.where(Trait.category.in_(categories))
        all_traits = list(db.execute(query).scalars())
        shareable = [t for t in all_traits if t.privacy_tier == "share"]
        blocked = len(all_traits) - len(shareable)


        _log_access(db, user_id, "get_profile_summary", _client_name(ctx), len(shareable), blocked)


        if not shareable:
            return "No shareable traits are available yet."
        return "\n".join(f"- [{t.category}] {t.value} ({t.summary or ''})" for t in shareable)
    finally:
        db.close()




@mcp_app.tool()
def get_preferences(category: str, ctx: Context | None = None) -> str:
    """Shareable traits for one category, with confidence."""
    user_id = _require_user_id()
    db = SessionLocal()
    try:
        all_traits = list(db.execute(select(Trait).where(Trait.user_id == user_id, Trait.category == category)).scalars())
        shareable = [t for t in all_traits if t.privacy_tier == "share"]
        blocked = len(all_traits) - len(shareable)


        _log_access(db, user_id, "get_preferences", _client_name(ctx), len(shareable), blocked)


        if not shareable:
            return f"No shareable traits for category '{category}'."
        return "\n".join(f"- {t.value} (confidence {t.confidence:.2f})" for t in shareable)
    finally:
        db.close()




@mcp_app.tool()
def search_memory(query: str, ctx: Context | None = None) -> str:
    """Most relevant shareable traits for a free-text query."""
    user_id = _require_user_id()
    db = SessionLocal()
    try:
        shareable = retrieve_relevant_traits(db, user_id, query, include_tiers=("share",), max_traits=5)


        matched_categories = categories_for_query(query)
        candidate_query = select(Trait).where(Trait.user_id == user_id)
        if matched_categories:
            candidate_query = candidate_query.where(Trait.category.in_(matched_categories))
        candidates = list(db.execute(candidate_query).scalars())
        blocked = len([t for t in candidates if t.privacy_tier != "share"])


        _log_access(db, user_id, "search_memory", _client_name(ctx), len(shareable), blocked)


        if not shareable:
            return "No relevant shareable traits found."
        return "\n".join(f"- [{t.category}] {t.value}" for t in shareable)
    finally:
        db.close()




@mcp_app.tool()
def request_private_info(category: str, ctx: Context | None = None) -> dict:
    """Request one-time access to Ask traits. Returns pending + secret receipt, not values.


    Ask the user to review Personal Nemo, then call check_approval. Expires in
    five minutes. Private traits and My Life are never eligible.
    """
    user_id = _require_user_id()
    token_hash = current_mcp_token_hash.get()
    if not token_hash:
        raise PermissionError("An authenticated bearer connection is required.")
    if category not in {item.value for item in Category}:
        raise ValueError("Unknown trait category")
    db = SessionLocal()
    try:
        withheld = list(db.execute(select(Trait).where(
            Trait.user_id == user_id, Trait.category == category, Trait.privacy_tier != "share"
        )).scalars())
        eligible = [trait for trait in withheld if trait.privacy_tier == "ask"]
        if not eligible:
            _log_access(db, user_id, "request_private_info", _client_name(ctx), 0, len(withheld))
            return {"status": "blocked", "message": "No Ask-tier traits available. Private information remains blocked."}
        receipt = secrets.token_urlsafe(32)
        request = MCPApproval(
            user_id=user_id, token_hash=token_hash,
            receipt_hash=hashlib.sha256(receipt.encode()).hexdigest(),
            client_name=_client_name(ctx), category=category,
            snapshot=approval_snapshot(eligible),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db.add(request)
        _log_access(db, user_id, "request_private_info", _client_name(ctx), 0, len(withheld))
        return {
            "status": "pending", "request_id": request.id, "receipt": receipt,
            "expires_at": request.expires_at.replace(tzinfo=timezone.utc).isoformat(),
            "message": "Review in Personal Nemo Privacy & MCP. Retry check_approval after approval; do not poll faster than every 5 seconds.",
        }
    finally:
        db.close()




@mcp_app.tool()
def check_approval(request_id: str, receipt: str, ctx: Context | None = None) -> dict:
    """Check a request using its secret receipt. Approved data is returned only once.


    Pending, denied, expired, invalidated, or consumed requests return no traits.
    A timeout after consumption requires a new approval, never automatic replay.
    """
    user_id = _require_user_id()
    db = SessionLocal()
    try:
        request = db.get(MCPApproval, request_id)
        if (request is None or request.user_id != user_id
                or request.token_hash != current_mcp_token_hash.get()
                or not secrets.compare_digest(request.receipt_hash, hashlib.sha256(receipt.encode()).hexdigest())):
            raise PermissionError("Approval request not found or not authorized.")
        refresh_approval(db, request)
        if request.status != "approved":
            return {"request_id": request.id, "status": request.status}
        result = db.execute(update(MCPApproval).where(
            MCPApproval.id == request.id, MCPApproval.status == "approved",
            MCPApproval.expires_at > datetime.now(timezone.utc),
            select(User.id).where(User.id == user_id, User.mcp_token_hash == request.token_hash).exists(),
            *[
                select(Trait.id).where(
                    Trait.id == item["id"], Trait.user_id == user_id, Trait.privacy_tier == "ask",
                    Trait.value == item["value"], Trait.summary == item["summary"],
                    Trait.category == item["category"],
                ).exists()
                for item in request.snapshot
            ],
        ).values(status="consumed").execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.rollback()
            return {"request_id": request.id, "status": "unavailable"}
        traits = request.snapshot
        _log_access(db, user_id, "check_approval", _client_name(ctx), len(traits), 0)
        return {"request_id": request.id, "status": "released", "traits": traits}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# ASGI app assembly + standalone entrypoint
# ---------------------------------------------------------------------------




def build_mcp_asgi_app() -> ASGIApp:
    inner = mcp_app.streamable_http_app()
    return BearerAuthMiddleware(inner)




def main() -> None:
    settings = get_settings()
    init_db()
    uvicorn.run(build_mcp_asgi_app(), host=settings.mcp_server_host, port=settings.mcp_server_port)




if __name__ == "__main__":
    main()



