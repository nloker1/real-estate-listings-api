"""Authenticated, read-only activity reporting. Never returns unsubscribe tokens."""
import hmac
import os
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.admin_auth import (COOKIE, COOKIE_PATH, SESSION_SECONDS, auth_config, issue_session,
                            origin_check, rate_limit, require_admin, secure_cookie, verify_password)
from app.services import admin_reporting

router = APIRouter(prefix="/api/admin", tags=["Admin"])
reports = APIRouter(dependencies=[Depends(require_admin)])


async def private_headers(request, call_next):
    try:
        response = await call_next(request)
    except SQLAlchemyError:
        if not request.url.path.startswith("/api/admin"):
            raise
        logging.getLogger(__name__).error("Admin reporting database unavailable")
        response = JSONResponse({"detail": "Reporting temporarily unavailable"}, status_code=503)
    if request.url.path.startswith("/api/admin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Vary"] = "Cookie, Origin"
    return response


async def admin_db():
    if os.getenv("APP_ENV", "").strip().lower() == "test":
        from app.database import AsyncSessionLocal, engine
        async with AsyncSessionLocal(bind=engine.execution_options(
            schema_translate_map={None: "admin_demo"}
        )) as session:
            yield session
        return
    from app.database import get_db
    async for session in get_db():
        yield session


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
async def login(body: Login, request: Request, response: Response):
    origin_check(request)
    username, password_hash, secret = auth_config()
    rate_limit(request)
    valid_password = await run_in_threadpool(verify_password, body.password, password_hash)
    if not valid_password or not hmac.compare_digest(body.username.encode(), username.encode()):
        raise HTTPException(401, "Invalid username or password", headers={"Cache-Control": "no-store"})
    token, session = issue_session(username, password_hash, secret)
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=secure_cookie(), samesite="strict", path=COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    return {"username": username, "csrf_token": session["csrf"], "expires_at": session["expires"]}


@router.get("/session")
async def session(admin=Depends(require_admin)):
    return {"username": admin["username"], "csrf_token": admin["csrf"], "expires_at": admin["expires"]}


@router.post("/logout")
async def logout(request: Request, response: Response, admin=Depends(require_admin)):
    origin_check(request)
    if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), admin["csrf"].encode()):
        raise HTTPException(403, "Invalid request token", headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=secure_cookie(), httponly=True, samesite="strict")
    return {"status": "signed_out"}


@reports.get("/clients")
async def clients(q: str = Query("", max_length=100), page: int = Query(1, ge=1, le=100000),
                  page_size: int = Query(25, ge=1, le=100), db=Depends(admin_db)):
    return await admin_reporting.clients(db, q.strip(), page, page_size)


@reports.get("/clients/{client_id}")
async def client(client_id: int, db=Depends(admin_db)):
    return await admin_reporting.client(db, client_id)


@reports.get("/clients/{client_id}/searches")
async def searches(client_id: int, page: int = Query(1, ge=1, le=100000),
                   page_size: int = Query(10, ge=1, le=100), db=Depends(admin_db)):
    return await admin_reporting.searches(db, client_id, page, page_size)


@reports.get("/clients/{client_id}/emails")
async def emails(client_id: int, page: int = Query(1, ge=1, le=100000),
                 page_size: int = Query(20, ge=1, le=100), db=Depends(admin_db)):
    return await admin_reporting.emails(db, client_id, page, page_size)


@reports.get("/clients/{client_id}/emails/{email_id}")
async def email(client_id: int, email_id: int, db=Depends(admin_db)):
    return await admin_reporting.email(db, client_id, email_id)


@reports.get("/clients/{client_id}/emails/{email_id}/events")
async def events(client_id: int, email_id: int, page: int = Query(1, ge=1, le=100000),
                 page_size: int = Query(25, ge=1, le=100), db=Depends(admin_db)):
    return await admin_reporting.events(db, client_id, email_id, page, page_size)


router.include_router(reports)
