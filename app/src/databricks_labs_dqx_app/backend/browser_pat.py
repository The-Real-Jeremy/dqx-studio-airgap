# ADDED by the dqx-studio-airgap build (not part of upstream DQX). See PATCHES.md.
"""Browser-held PAT mode (``DQX_BROWSER_PAT=1``) for workspaces where the app's service principal has no grants.

Each user enters their own personal access token once. The back end checks that it belongs to the signed-in
viewer, seals it (AES-GCM; the key never leaves this process, see key_scope) and returns it as an HttpOnly, Secure,
SameSite=Strict cookie. Page JavaScript cannot read it, and nothing about it is kept in the container, env, logs
or tables. On every request that needs it, the cookie is unsealed in memory and used only for the Jobs API
(``run_now`` / ``get_run`` / ``cancel``) and the admin group list. Everything SQL / Unity Catalog runs on the
viewer's own on-behalf-of token. A PAT that stops working (expired or revoked) clears the cookie and asks again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Annotated

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import PermissionDenied, Unauthenticated
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel

from .logger import logger

COOKIE = "dqx_studio_pat"
MAX_AGE_S = 30 * 24 * 3600  # a forgotten browser does not hold a PAT for ever
_AAD = b"dqx-studio-browser-pat-v1"
_CHECK_TTL_S = 300  # re-validate a PAT with Databricks at most every 5 minutes per user
_KEY: bytes | None = None


def enabled() -> bool:
    return os.environ.get("DQX_BROWSER_PAT", "").strip().lower() in ("1", "true", "yes")


def key_scope() -> str:
    """Where the sealing key lives.

    "app": derived from the app's own client secret, so a sealed cookie survives restarts. Used only when DEPLOY
    sets DQX_PAT_KEY_SCOPE=app, which it does only for a standalone app: there the secret belongs to Studio alone.
    "process" (the default, and always inside App-ception): a random key generated in memory at start-up. Inside
    App-ception every child shares the host app's client secret and name, so a derived key could be recomputed by any
    sibling child; a per-process random key cannot. Users then re-enter their PAT after Studio restarts.
    """
    if os.environ.get("APPCEPTION_CHILD") or os.environ.get("CATALOG_CHILD"):
        return "process"
    if os.environ.get("DQX_PAT_KEY_SCOPE", "").strip().lower() == "app" and os.environ.get("DATABRICKS_CLIENT_SECRET"):
        return "app"
    return "process"


def _key() -> bytes:
    """32-byte AES key; never leaves this process (see key_scope)."""
    global _KEY  # noqa: PLW0603
    if _KEY is None:
        if key_scope() == "app":
            info = (os.environ.get("DATABRICKS_APP_NAME") or "dqx-studio").encode()
            _KEY = HKDF(algorithm=hashes.SHA256(), length=32, salt=_AAD, info=info).derive(
                os.environ["DATABRICKS_CLIENT_SECRET"].encode())
        else:
            _KEY = AESGCM.generate_key(bit_length=256)
    return _KEY


def _in_app() -> bool:
    """Running on Databricks Apps (or inside App-ception): the proxy always sends the viewer's identity."""
    return bool(os.environ.get("DATABRICKS_APP_NAME") or os.environ.get("APPCEPTION_CHILD") or os.environ.get("CATALOG_CHILD"))


def _no_identity() -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "no_identity",
                                                  "message": "Sign in again: the app did not receive your identity."})



def seal(pat: str, email: str) -> str:
    nonce = secrets.token_bytes(12)
    blob = json.dumps({"t": pat, "e": email.lower(), "iat": int(time.time())}).encode()
    return base64.urlsafe_b64encode(nonce + AESGCM(_key()).encrypt(nonce, blob, _AAD)).decode().rstrip("=")


def unseal(value: str) -> dict | None:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        return json.loads(AESGCM(_key()).decrypt(raw[:12], raw[12:], _AAD))
    except Exception:  # tampered, from another app, or sealed under a key that has since changed
        return None


def _cookie_header(value: str, max_age: int) -> str:
    # Path=/ : standalone the app owns the origin; as a catalog child the catalog rewrites Path to the child's prefix
    # (prefix mode). SameSite=Strict: never sent on a cross-site request.
    return f"{COOKIE}={value}; Max-Age={max_age}; Path=/; HttpOnly; Secure; SameSite=Strict"


CLEAR_COOKIE = _cookie_header("", 0)


def _host() -> str:
    h = os.environ.get("DATABRICKS_HOST", "")
    return h if h.startswith("http") else f"https://{h}"


def _client(pat: str) -> WorkspaceClient:
    return WorkspaceClient(host=_host(), token=pat, auth_type="pat")


def _pat_user(pat: str) -> str:
    """The PAT's own user name; raises Unauthenticated/PermissionDenied if it does not work."""
    return (_client(pat).current_user.me().user_name or "").lower()


_checked: dict[str, float] = {}  # sha256(pat) -> time last confirmed working (no PAT values kept)
_CHECKED_MAX = 1000


def _mark_checked(h: str) -> None:
    if len(_checked) >= _CHECKED_MAX:  # prune what is stale, then (if still full) start over: it is only a cache
        now = time.time()
        for k in [k for k, t in _checked.items() if now - t > _CHECK_TTL_S]:
            _checked.pop(k, None)
        if len(_checked) >= _CHECKED_MAX:
            _checked.clear()
    _checked[h] = time.time()


def need_pat(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": code, "message": message}, headers={"Set-Cookie": CLEAR_COOKIE})


def ws_from_cookie(request: Request, viewer_email: str | None) -> WorkspaceClient:
    """WorkspaceClient for this request, built from the viewer's own sealed PAT cookie."""
    if not viewer_email and _in_app():
        raise _no_identity()  # fail closed: never use a sealed token without knowing who is asking
    sealed = request.cookies.get(COOKIE)
    data = unseal(sealed) if sealed else None
    if not data or not data.get("t"):
        raise need_pat("pat_required", "Enter your personal access token to run DQX jobs.")
    if time.time() - float(data.get("iat", 0)) > MAX_AGE_S:
        raise need_pat("pat_expired", "Your saved token is more than 30 days old. Enter it again.")
    if viewer_email and data.get("e") != viewer_email.lower():
        raise need_pat("pat_required", "The saved token belongs to another user. Enter your own personal access token.")
    pat = data["t"]
    h = hashlib.sha256(pat.encode()).hexdigest()
    if time.time() - _checked.get(h, 0) > _CHECK_TTL_S:
        try:
            _pat_user(pat)
        except (Unauthenticated, PermissionDenied):
            _checked.pop(h, None)
            raise need_pat("pat_expired", "Your personal access token has expired or been revoked. Enter a new one.")
        _mark_checked(h)
    request.state.used_browser_pat = True
    return _client(pat)


# ── API ──────────────────────────────────────────────────────────────────────────────────────────────────────

router = APIRouter()


class PatIn(BaseModel):
    pat: str


@router.get("/pat/status", operation_id="getPatStatus")
def pat_status(request: Request, x_forwarded_email: Annotated[str | None, Header()] = None) -> dict:
    if not enabled():
        return {"mode": "service_principal", "has_pat": True, "valid": True, "schedules": "on"}
    data = unseal(request.cookies.get(COOKIE, "")) if request.cookies.get(COOKIE) else None
    if not x_forwarded_email and _in_app():
        data = None
    mine = bool(data and (not x_forwarded_email or data.get("e") == x_forwarded_email.lower()))
    valid = False
    if mine:
        try:
            ws_from_cookie(request, x_forwarded_email)
            valid = True
        except HTTPException:
            valid = False
    return {"mode": "browser_pat", "has_pat": mine, "valid": valid, "viewer": x_forwarded_email,
            "schedules": "off", "cookie_max_age_days": MAX_AGE_S // 86400, "key_scope": key_scope()}


@router.post("/pat", operation_id="savePat")
def save_pat(body: PatIn, response: Response, x_forwarded_email: Annotated[str | None, Header()] = None) -> dict:
    if not enabled():
        raise HTTPException(status_code=400, detail="This app does not use browser-held tokens.")
    if not x_forwarded_email and _in_app():
        raise _no_identity()
    pat = body.pat.strip()
    if not pat:
        raise HTTPException(status_code=400, detail="Paste a personal access token.")
    try:
        owner = _pat_user(pat)
    except (Unauthenticated, PermissionDenied):
        raise HTTPException(status_code=400, detail="Databricks rejected that token: it is mistyped, expired or revoked.")
    except Exception as e:  # network / host problems: say so, never echo the token
        logger.warning("PAT check failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="Could not reach the workspace to check the token. Try again.")
    if x_forwarded_email and owner != x_forwarded_email.lower():
        raise HTTPException(status_code=403,
                            detail=f"That token belongs to {owner}, but you are signed in as {x_forwarded_email}. "
                                   "Use a token you created yourself (Settings > Developer > Access tokens).")
    _mark_checked(hashlib.sha256(pat.encode()).hexdigest())
    response.headers["Set-Cookie"] = _cookie_header(seal(pat, owner), MAX_AGE_S)
    logger.info("Browser PAT saved for %s", owner)
    return {"ok": True, "user": owner, "cookie_max_age_days": MAX_AGE_S // 86400}


@router.delete("/pat", operation_id="forgetPat")
def forget_pat(response: Response) -> dict:
    response.headers["Set-Cookie"] = CLEAR_COOKIE
    return {"ok": True}
