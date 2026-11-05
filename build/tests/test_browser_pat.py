import os, sys
os.environ.update({"DQX_BROWSER_PAT": "1", "DATABRICKS_CLIENT_SECRET": "unit-test-secret", "DATABRICKS_HOST": "https://example.invalid",
                   "DATABRICKS_APP_NAME": "dqx-test"})
sys.path.insert(0, sys.argv[1])
from fastapi import FastAPI
from fastapi.testclient import TestClient
from databricks.sdk.errors import Unauthenticated
from databricks_labs_dqx_app.backend import browser_pat as bp

owners = {"good-pat": "alice@example.com", "bob-pat": "bob@example.com"}
def fake_user(pat):
    if pat not in owners: raise Unauthenticated("bad")
    return owners[pat]
bp._pat_user = fake_user
bp._client = lambda pat: ("client-for", pat)

app = FastAPI(); app.include_router(bp.router, prefix="/api/v1")
from fastapi import Depends, Request
@app.get("/api/v1/needs-pat")
def needs(request: Request):
    from fastapi import Header
    ws = bp.ws_from_cookie(request, request.headers.get("x-forwarded-email"))
    return {"ok": True}
c = TestClient(app, base_url="https://testserver")
H = {"X-Forwarded-Email": "Alice@example.com"}
ok = 0
def check(cond, msg):
    global ok
    assert cond, msg; ok += 1; print("PASS", msg)

r = c.get("/api/v1/pat/status", headers=H); check(r.json()["has_pat"] is False and r.json()["mode"] == "browser_pat", "no cookie -> has_pat false")
r = c.get("/api/v1/needs-pat", headers=H); check(r.status_code == 401 and r.json()["detail"]["code"] == "pat_required", "needs-pat without cookie -> 401 pat_required")
r = c.post("/api/v1/pat", json={"pat": "nope"}, headers=H); check(r.status_code == 400 and "nope" not in r.text, "bad PAT -> 400, token not echoed")
r = c.post("/api/v1/pat", json={"pat": "bob-pat"}, headers=H); check(r.status_code == 403 and "bob@example.com" in r.text, "someone else's PAT -> 403 naming owner")
r = c.post("/api/v1/pat", json={"pat": "good-pat"}, headers=H)
sc = r.headers.get("set-cookie", "")
check(r.status_code == 200, "own PAT -> 200")
check(all(f in sc for f in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=2592000")), "cookie flags HttpOnly Secure SameSite=Strict Path=/ 30d")
val = sc.split(";")[0].split("=", 1)[1]
check("good-pat" not in sc and "alice" not in sc, "cookie value does not contain the PAT or email in clear")
check(bp.unseal(val)["t"] == "good-pat", "cookie unseals to the PAT inside the app")
c.cookies.set(bp.COOKIE, val)
r = c.get("/api/v1/pat/status", headers=H); check(r.json()["has_pat"] and r.json()["valid"], "status with cookie -> valid")
r = c.get("/api/v1/needs-pat", headers=H); check(r.status_code == 200, "needs-pat with cookie -> 200")
r = c.get("/api/v1/needs-pat", headers={"X-Forwarded-Email": "bob@example.com"}); check(r.status_code == 401, "cookie used under another viewer -> 401")
tampered = val[:-4] + ("AAAA" if not val.endswith("AAAA") else "BBBB")
c.cookies.set(bp.COOKIE, tampered); r = c.get("/api/v1/needs-pat", headers=H); check(r.status_code == 401, "tampered cookie -> 401")
# expiry: PAT revoked after save
c.cookies.set(bp.COOKIE, val); owners.pop("good-pat"); bp._checked.clear()
r = c.get("/api/v1/needs-pat", headers=H)
check(r.status_code == 401 and r.json()["detail"]["code"] == "pat_expired" and "Max-Age=0" in r.headers.get("set-cookie", ""), "revoked PAT -> 401 pat_expired + cookie cleared")
r = c.delete("/api/v1/pat"); check("Max-Age=0" in r.headers.get("set-cookie", ""), "forget -> cookie cleared")
# other app key: a cookie sealed under another app's key does not open
os.environ["DATABRICKS_APP_NAME"] = "other-app"; bp._KEY = None
check(bp.unseal(val) is None, "cookie from another app name does not unseal")
# --- nice-to-haves ---
os.environ["DATABRICKS_APP_NAME"] = "dqx-test"; bp._KEY = None
owners["good-pat"] = "alice@example.com"; bp._checked.clear()
r = c.post("/api/v1/pat", json={"pat": "good-pat"}, headers=H); val = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
c.cookies.set(bp.COOKIE, val)
r = c.get("/api/v1/needs-pat"); check(r.status_code == 401 and r.json()["detail"]["code"] == "no_identity", "in an app, no X-Forwarded-Email -> 401 no_identity (fail closed)")
r = c.post("/api/v1/pat", json={"pat": "good-pat"}); check(r.status_code == 401, "save without identity -> 401")
import json as _j, secrets as _s, base64 as _b, time as _t
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
n = _s.token_bytes(12); old = _b.urlsafe_b64encode(n + AESGCM(bp._key()).encrypt(n, _j.dumps({"t": "good-pat", "e": "alice@example.com", "iat": int(_t.time()) - 31 * 86400}).encode(), bp._AAD)).decode().rstrip("=")
c.cookies.set(bp.COOKIE, old); r = c.get("/api/v1/needs-pat", headers=H)
check(r.status_code == 401 and r.json()["detail"]["code"] == "pat_expired", "sealed value older than 30 days -> rejected")
bp._checked.clear(); [bp._checked.__setitem__(str(i), 0.0) for i in range(bp._CHECKED_MAX)]
bp._mark_checked("fresh"); check(len(bp._checked) <= 2, f"check map capped (now {len(bp._checked)} entries)")
check(bp.key_scope() == "process", "default key scope is process (random, in memory)")
print(f"{ok} checks passed (expected 20)")
