# ADDED by the dqx-studio-airgap build (not part of upstream DQX). See PATCHES.md.
"""Per-user access check (air-gap build): what the signed-in user is missing to use Studio, each with the exact fix.

``GET /api/v1/access-check`` runs small, side-effect-free probes as the viewer:
  - SQL warehouse: ``SELECT 1`` on the configured warehouse (CAN USE)
  - Studio's schema: read one Studio table (USE CATALOG, USE SCHEMA, SELECT), a no-op ``DELETE ... WHERE false``
    (MODIFY), and create + drop a probe view (CREATE TABLE, which every run needs for its temp view)
  - the task-runner job, with the viewer's own PAT: can they see it, and do they hold CAN MANAGE RUN
  - the PAT itself (missing / expired)
Each result is ok / missing / unknown. "unknown" means the probe could not decide; it is never shown as a pass.
"""

from __future__ import annotations

import hashlib
import os
from typing import Annotated, Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState
from fastapi import APIRouter, Header, HTTPException, Request

from . import browser_pat
from .config import conf
from .logger import logger

router = APIRouter()
_RUN_LEVELS = {"CAN_MANAGE_RUN", "CAN_MANAGE", "IS_OWNER"}


def _q(name: str) -> str:
    return "`" + name.replace("`", "") + "`"


def _sql(ws: WorkspaceClient, warehouse_id: str, statement: str) -> tuple[bool, str]:
    r = ws.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=statement, wait_timeout="30s")
    st = r.status.state if r.status else None
    if st == StatementState.SUCCEEDED:
        return True, ""
    err = (r.status.error.message if r.status and r.status.error else "") or str(st)
    return False, err


def _item(check: str, ok: bool | None, detail: str, fix: str = "") -> dict[str, Any]:
    return {"check": check, "status": {True: "ok", False: "missing", None: "unknown"}[ok], "detail": detail, "fix": fix}


@router.get("/access-check", operation_id="accessCheck")
def access_check(
    request: Request,
    x_forwarded_email: Annotated[str | None, Header()] = None,
    x_forwarded_access_token: Annotated[str | None, Header(alias="X-Forwarded-Access-Token")] = None,
) -> dict[str, Any]:
    me = x_forwarded_email or "you"
    cat, sch = conf.catalog, conf.schema_name
    fq = f"{_q(cat)}.{_q(sch)}"
    wh = os.environ.get("DATABRICKS_WAREHOUSE_ID") or os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID") or ""
    out: list[dict[str, Any]] = []
    if not x_forwarded_access_token:
        raise HTTPException(status_code=401, detail="Sign in again: the app did not receive your identity.")
    obo = WorkspaceClient(token=x_forwarded_access_token, auth_type="pat")

    # 1. warehouse
    try:
        ok, err = _sql(obo, wh, "SELECT 1")
        out.append(_item("SQL warehouse (CAN USE)", ok, "can run queries" if ok else err[:300],
                         "" if ok else f"Ask the warehouse owner: SQL Warehouses > the warehouse (id {wh}) > Permissions > "
                                       f"add {me} (or the group 'users') with 'Can use'."))
    except Exception as e:  # e.g. PermissionDenied on the warehouse itself
        out.append(_item("SQL warehouse (CAN USE)", False, str(e)[:300],
                         f"Ask the warehouse owner: SQL Warehouses > the warehouse (id {wh}) > Permissions > add {me} with 'Can use'."))
    warehouse_ok = out[-1]["status"] == "ok"

    grants = (f"GRANT USE CATALOG ON CATALOG {_q(cat)} TO `{me}`;\n"
              f"GRANT USE SCHEMA, CREATE TABLE ON SCHEMA {fq} TO `{me}`;\n"
              f"-- and on each of Studio's dq_* tables: GRANT SELECT, MODIFY ON TABLE {fq}.<table> TO `{me}`;\n"
              "(the DEPLOY notebook prints the full list, and can apply it for every user)")
    if warehouse_ok:
        # 2. read (USE CATALOG + USE SCHEMA + SELECT)
        ok, err = _sql(obo, wh, f"SELECT 1 FROM {fq}.dq_quality_rules LIMIT 0")
        out.append(_item("Read Studio's tables (USE CATALOG, USE SCHEMA, SELECT)", ok, "ok" if ok else err[:300],
                         "" if ok else "Ask the owner of the schema to run:\n" + grants))
        # 3. write (MODIFY); a DELETE that matches nothing changes no data
        if ok:
            ok2, err2 = _sql(obo, wh, f"DELETE FROM {fq}.dq_comments WHERE 1 = 0")
            out.append(_item("Write Studio's tables (MODIFY)", ok2, "ok" if ok2 else err2[:300],
                             "" if ok2 else "Ask the owner of the schema (or the DEPLOY installer) to run:\n" + grants))
        # 4. create the per-run temporary view (CREATE TABLE on the schema)
        probe = "dqx_access_probe_" + hashlib.sha256(me.encode()).hexdigest()[:10]
        ok3, err3 = _sql(obo, wh, f"CREATE OR REPLACE VIEW {fq}.{probe} AS SELECT 1 AS x")
        if ok3:
            _sql(obo, wh, f"DROP VIEW IF EXISTS {fq}.{probe}")
        out.append(_item("Create the per-run view (CREATE TABLE on the schema)", ok3, "ok" if ok3 else err3[:300],
                         "" if ok3 else f"Ask the owner of the schema to run:\nGRANT CREATE TABLE ON SCHEMA {fq} TO `{me}`;"))
    else:
        out.append(_item("Studio's schema and tables", None, "not checked: needs the SQL warehouse first", ""))

    # 5. PAT + job
    job_name = os.environ.get("DQX_JOB_NAME") or f"job {conf.job_id}"
    if browser_pat.enabled():
        try:
            pat_ws = browser_pat.ws_from_cookie(request, x_forwarded_email)
            out.append(_item("Personal access token", True, "saved in this browser and working"))
        except HTTPException as e:
            msg = e.detail.get("message") if isinstance(e.detail, dict) else str(e.detail)
            out.append(_item("Personal access token", False, msg,
                             "Click 'Enter token' and paste a PAT from Settings > Developer > Access tokens."))
            pat_ws = None
    else:
        pat_ws = None
    if pat_ws is not None and conf.job_id:
        try:
            pat_ws.jobs.get(int(conf.job_id))
            level = None
            try:
                acl = pat_ws.api_client.do("GET", f"/api/2.0/permissions/jobs/{conf.job_id}")
                me_obj = pat_ws.current_user.me()
                groups = {g.display for g in (me_obj.groups or []) if g.display} | {"users"}
                for e in acl.get("access_control_list", []):
                    if e.get("user_name", "").lower() == me.lower() or e.get("group_name") in groups:
                        for p in e.get("all_permissions", []):
                            if p.get("permission_level") in _RUN_LEVELS:
                                level = p["permission_level"]
                ok = level is not None
                detail = f"you hold {level}" if ok else "you can see the job but cannot run it"
            except Exception as e:  # reading the ACL can itself be refused; then we cannot tell
                ok, detail = None, f"could not read the job's permissions ({type(e).__name__})"
            out.append(_item(f"Run the task-runner job (CAN MANAGE RUN on {job_name})", ok, detail,
                             "" if ok else "Ask the installer to re-run DEPLOY (grant_job_run_to_users = yes), or: Jobs & "
                                           f"Pipelines > {job_name} > Permissions > add {me} with 'Can Manage Run'."))
        except Exception as e:
            out.append(_item(f"Run the task-runner job (CAN MANAGE RUN on {job_name})", False,
                             f"you cannot see the job ({type(e).__name__})",
                             "Ask the installer to re-run DEPLOY (grant_job_run_to_users = yes), or: Jobs & Pipelines > "
                             f"{job_name} > Permissions > add {me} with 'Can Manage Run'."))
    missing = [i for i in out if i["status"] == "missing"]
    logger.info("access check for %s: %d missing, %d unknown", me, len(missing), sum(i["status"] == "unknown" for i in out))
    return {"user": me, "all_ok": not missing and all(i["status"] == "ok" for i in out), "checks": out}
