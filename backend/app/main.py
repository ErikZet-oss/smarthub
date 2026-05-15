import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# backend/.env — rovnaký SMARTHUB_AUTH_SECRET ako vo frontend/.env.local
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import jwt
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

# Playwright spúšťa podproces (Node driver). Na Windows len ProactorEventLoop
# podporuje asyncio.create_subprocess_exec — inak NotImplementedError.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.api.deps import AuthUserContext
from app.api.routes import router
from app.db import DATABASE_FILE, create_db_and_tables, engine, migrate_sqlite_schema
from app.services.dev_run_log import dev_run_log, dev_screens_dir
from app.services.product_images import product_images_dir
from app.services.smarthub_bootstrap import seed_initial_admin_if_empty
from app.services.supplier_logos import seed_supplier_logos_from_repo, supplier_logos_dir

app = FastAPI(title="Smarthub API")
# Lokálna sieť (192.168…): inak prehliadač pri fetch z Nextu na IP zobrazí „Failed to fetch“ (CORS).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=(
        r"^(https?://(localhost|127\.0\.0\.1|\[::1\]|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d+)?"
        r"|https://[a-z0-9-]+\.onrender\.com)$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
app.mount("/dev-assets", StaticFiles(directory=dev_screens_dir()), name="dev-assets")
app.mount(
    "/supplier-logos",
    StaticFiles(directory=supplier_logos_dir()),
    name="supplier-logos",
)
app.mount(
    "/product-images",
    StaticFiles(directory=product_images_dir()),
    name="product-images",
)


@app.middleware("http")
async def smarthub_bearer_auth(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api"):
        return await call_next(request)
    if path in ("/api/health", "/api/auth/smarthub-login"):
        return await call_next(request)
    auth = (request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return JSONResponse(
            {
                "detail": (
                    "Chýba Authorization: Bearer token. "
                    "Otvor aplikáciu cez Next.js a prihlás sa."
                )
            },
            status_code=401,
        )
    token = auth[7:].strip()
    secret = os.environ.get("SMARTHUB_AUTH_SECRET", "")
    if len(secret) < 16:
        return JSONResponse(
            {"detail": "Na API nastav SMARTHUB_AUTH_SECRET (min. 16 znakov)."},
            status_code=500,
        )
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        uid = payload.get("uid")
        if uid is None:
            return JSONResponse(
                {"detail": "Token bez uid — prihlás sa znova v aplikácii."},
                status_code=401,
            )
        request.state.smarthub_user = AuthUserContext(
            id=int(uid),
            username=str(payload.get("sub") or ""),
            is_admin=payload.get("role") == "admin",
        )
    except jwt.PyJWTError:
        return JSONResponse(
            {"detail": "Neplatný alebo expirovaný token."},
            status_code=401,
        )
    return await call_next(request)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    migrate_sqlite_schema()
    with Session(engine) as session:
        seed_initial_admin_if_empty(session)
        seed_supplier_logos_from_repo(session)
        session.commit()
    dev_run_log(
        "api",
        f"SQLite databáza: {DATABASE_FILE}",
        "info",
    )
    dev_run_log(
        "api",
        "API štart — Dev panel číta len tento proces (reštart = prázdny buffer). "
        "Trvalý záznam: backend/data/dev_automation.ndjson",
        "info",
    )
