"""
Personal Nemo backend entrypoint.


Run with: uvicorn app.main:app --reload --port 8000
"""
import logging
from time import monotonic
from collections import deque
from threading import Lock


from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import APIError
from starlette.types import ASGIApp, Message, Receive, Scope, Send


from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services import get_or_create_demo_user, load_all_demo_data
from app.pipeline import BudgetExceededError, EmailProcessingError
from app.routes import account_router, chat_router, connections_router, privacy_router, profile_router, qa_router, usage_router


app = FastAPI(title="Personal Nemo API", version="0.1.0")


_demo_ai_lock = Lock()
_demo_ai_requests: deque[float] = deque()
_DEMO_AI = {("POST", "/api/chat"), ("POST", "/api/chat/compare"), ("GET", "/api/nudge")}
_DEMO_READS = {
    "/health", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc",
    "/api/connections", "/api/profile", "/api/profile/activity", "/api/profile/my-life",
    "/api/today", "/api/qa/topics", "/api/privacy", "/api/mcp/log", "/api/mcp/approvals", "/api/usage",
}




class PublicDemoGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app


    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not get_settings().public_demo:
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        paid = (method, path) in _DEMO_AI
        allowed = paid or (method == "POST" and path == "/api/demo/load") or (
            method in ("GET", "HEAD") and (path in _DEMO_READS or path.startswith("/api/traits/"))
        )
        if not allowed:
            await JSONResponse(status_code=403, content={
                "detail":"Public sample demo: live connections and personal-data changes are available only in a private installation."
            })(scope, receive, send)
            return
        if not paid:
            await self.app(scope, receive, send)
            return
        if not get_settings().nebius_api_key.strip():
            await JSONResponse({"detail":"AI is disabled on this public demo: the server's NEBIUS_API_KEY is not configured."},
                               status_code=503)(scope, receive, send)
            return
        if method == "POST":
            request = Request(scope, receive)
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                await JSONResponse({"detail":"Invalid request size."}, status_code=400)(scope, receive, send)
                return
            if length < 0:
                await JSONResponse({"detail":"Content-Length is required for public demo AI requests."}, status_code=411)(scope, receive, send)
                return
            if length > 16384:
                await JSONResponse({"detail":"Public demo AI requests must be at most 16 KB."}, status_code=413)(scope, receive, send)
                return
            body = await request.body()
            if len(body) != length:
                await JSONResponse({"detail":"Request size does not match Content-Length."}, status_code=400)(scope, receive, send)
                return
            delivered = False
            original_receive = receive


            async def replay() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type":"http.request", "body":body, "more_body":False}
                return await original_receive()
            receive = replay
        if not _demo_ai_lock.acquire(blocking=False):
            await JSONResponse({"detail":"Another demo AI request is running. Please try again shortly."},status_code=429,
                               headers={"Retry-After":"5"})(scope, receive, send)
            return
        try:
            now = monotonic()
            while _demo_ai_requests and _demo_ai_requests[0] <= now - 60:
                _demo_ai_requests.popleft()
            if len(_demo_ai_requests) >= 10:
                await JSONResponse({"detail":"Public demo AI rate limit reached. Try again in a minute."},status_code=429,
                                   headers={"Retry-After":"60"})(scope, receive, send)
                return
            _demo_ai_requests.append(now)
            # Keep the lease until the ASGI stream completes or disconnects, not just until headers arrive.
            await self.app(scope, receive, send)
        finally:
            _demo_ai_lock.release()




app.add_middleware(PublicDemoGuard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Used-Trait-Ids","X-Web-Search-Used","X-Web-Sources"],
)




# Starlette moves handlers registered for the bare `Exception` type to the
# outermost ServerErrorMiddleware, which sits *outside* CORSMiddleware - so a
# generic catch-all here would actually make CORS headers go missing, not fix
# it. Instead, register handlers for the specific exception types expected
# while Nebius isn't configured yet (or the spend cap trips); these stay in
# Starlette's inner ExceptionMiddleware, so CORS headers are preserved and the
# frontend gets a real JSON error instead of an opaque CORS failure.
@app.exception_handler(APIError)
async def openai_error_handler(request: Request, exc: APIError) -> JSONResponse:
    logging.getLogger(__name__).warning("Nebius request failed (%s).", type(exc).__name__)
    return JSONResponse(status_code=502, content={"detail": "Nebius Token Factory request failed. Check provider configuration, quota and server logs."})




@app.exception_handler(BudgetExceededError)
async def budget_exceeded_handler(request: Request, exc: BudgetExceededError) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": str(exc)})




@app.exception_handler(EmailProcessingError)
async def email_processing_error_handler(request: Request, exc: EmailProcessingError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})




@app.on_event("startup")
def on_startup() -> None:
    settings = get_settings()
    if settings.app_env == "production" and not settings.public_demo:
        raise RuntimeError("Public production hosting requires PUBLIC_DEMO=true. Private hosting needs dashboard authentication first.")
    init_db()
    if settings.public_demo:
        with SessionLocal() as db:
            user = get_or_create_demo_user(db)
            load_all_demo_data(db, user.id)




@app.get("/health")
def health() -> dict:
    return {"status": "ok"}




app.include_router(connections_router)
app.include_router(profile_router)
app.include_router(chat_router)
app.include_router(qa_router)
app.include_router(privacy_router)
app.include_router(usage_router)
app.include_router(account_router)