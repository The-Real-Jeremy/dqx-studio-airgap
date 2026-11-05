# Databricks notebook source
# MAGIC %md
# MAGIC # Deploy DQX Studio (child and PAT edition, browser only)
# MAGIC
# MAGIC Two ways, chosen with the **mode** widget:
# MAGIC - **child**: add Studio to an App-ception app you deployed from `app-ception-main/`. It copies this folder into
# MAGIC   its `child_apps/dqx_studio/`, adds the scopes Studio needs to the App-ception app, and redeploys it.
# MAGIC - **standalone**: deploy Studio as its own Databricks App.
# MAGIC
# MAGIC **Run it on the existing classic all-purpose cluster** (DBR 15.4 LTS or 16.4 LTS): the task-runner job uses
# MAGIC the same cluster. It needs no CLI, pip or npm, and no internet. It does the following:
# MAGIC 1. checks the wheel packs you imported next to this folder (`dqx-studio-wheels-<k>-of-<n>`);
# MAGIC 2. creates Studio's tables in your existing schema (as you; never `CREATE SCHEMA`);
# MAGIC 3. creates the task-runner job on the cluster, and lets every workspace user run it (widget 10);
# MAGIC 4. prints, and optionally applies, the grants every user needs on Studio's schema (widget 11);
# MAGIC 5. deploys the app.
# MAGIC
# MAGIC No token goes anywhere: each user enters their own PAT in the app the first time they open it, and it is
# MAGIC kept only in their browser. **Scheduler is disabled in dynamic PAT mode.** Re-running this notebook is safe.

# COMMAND ----------

import os, re, json, time
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
SRC = "/Workspace" + os.path.dirname(ctx.notebookPath().get())   # this repo folder, whatever its name
PARENT = os.path.dirname(SRC)
# The App-ception folder: app-ception-main, then app-catalog-main (its old name), then the first sibling folder that
# has catalog/server.mjs. A renamed-aside old copy only wins if nothing better is there.
_cands = [d for d in sorted(os.listdir(PARENT)) if os.path.isfile(os.path.join(PARENT, d, "catalog", "server.mjs"))]
guess = next((os.path.join(PARENT, d) for d in ("app-ception-main", "app-catalog-main") if d in _cands),
             os.path.join(PARENT, _cands[0]) if _cands else "")
me = json.loads(ctx.safeToJson())["attributes"]["user"]

dbutils.widgets.dropdown("mode", "child", ["child", "standalone"], "1. Mode")
dbutils.widgets.text("catalog_folder", guess, "2. (child) App-ception folder")
dbutils.widgets.text("catalog_app_name", "app-ception", "3. (child) App-ception app name")
dbutils.widgets.text("standalone_app_name", "dqx-studio", "4. (standalone) App name")
dbutils.widgets.text("budget_policy_id", "", "5. (standalone) Budget policy id, if required")
dbutils.widgets.text("catalog", "", "6. Catalog (existing)")
dbutils.widgets.text("schema", "", "7. Schema (existing; Studio's tables go here)")
dbutils.widgets.text("warehouse_id", "", "8. SQL warehouse id (existing)")
dbutils.widgets.text("admin_group", "users", "9. Studio admin group")
dbutils.widgets.dropdown("grant_job_run_to_users", "yes", ["yes", "no"], "10. Let all users run the task-runner job")
dbutils.widgets.dropdown("apply_schema_grants", "no", ["no", "yes"], "11. Apply the schema grants for all users")
dbutils.widgets.text("install_folder", f"/Workspace/Users/{me}/dqx_studio_install", "12. Install folder (job + cluster wheels)")

W = {k: dbutils.widgets.get(k).strip() for k in ["mode", "catalog_folder", "catalog_app_name", "standalone_app_name",
     "budget_policy_id", "catalog", "schema", "warehouse_id", "admin_group", "grant_job_run_to_users",
     "apply_schema_grants", "install_folder"]}
for k in ("catalog", "schema", "warehouse_id"):
    assert W[k], f"widget '{k}' must be set (see the README, step 3)"
CLUSTER_ID = json.loads(ctx.safeToJson())["attributes"].get("clusterId")
assert CLUSTER_ID, "attach this notebook to the existing classic all-purpose cluster (the job runs on it)"
APP_NAME = W["catalog_app_name"] if W["mode"] == "child" else W["standalone_app_name"]
JOB_NAME = f"{APP_NAME}-dqx-studio-task-runner" if W["mode"] == "child" else f"{APP_NAME}-task-runner"
print("this folder:", SRC, "| mode:", W["mode"], "| app:", APP_NAME, "| cluster:", CLUSTER_ID, "| installer:", me)

# COMMAND ----------

# MAGIC %md ## 1. Check the wheel packs

# COMMAND ----------

import hashlib
man = json.load(open(f"{SRC}/wheels.json"))
N = man["packs"]
packs = {}
for k in range(1, N + 1):
    name = f"dqx-studio-wheels-{k}-of-{N}"
    hits = [d for d in (os.path.join(PARENT, name), os.path.join(SRC, name)) if os.path.isdir(d)]
    packs[k] = hits[0] if hits else None
missing = [f"dqx-studio-wheels-{k}-of-{N}.zip" for k, d in packs.items() if not d]
assert not missing, (f"Import these wheel packs into {PARENT} first (Workspace > Import, one ZIP each): "
                     + ", ".join(missing))
bad, WHEEL = [], {}
for f in man["files"]:
    p = os.path.join(packs[f["pack"]], f["target"], f["name"])
    if not os.path.isfile(p) or os.path.getsize(p) != f["size"] or hashlib.sha256(open(p, "rb").read()).hexdigest() != f["sha256"]:
        bad.append(f["name"])
    WHEEL[(f["target"], f["name"])] = p
assert not bad, f"{len(bad)} wheel(s) missing or corrupt, re-import their pack: {bad[:5]}"
print(f"{len(man['files'])} wheels in {N} packs verified (sha256): "
      f"{sum(t == 'app' for t, _ in WHEEL)} app, {sum(t == 'cluster' for t, _ in WHEEL)} cluster")

# COMMAND ----------

# MAGIC %md ## 2. Install the folder for the job, and DQX for this notebook (offline)

# COMMAND ----------

import shutil, subprocess, sys
from databricks.sdk import WorkspaceClient as _WC

def replace_folder(dest):
    """Keep one previous copy as <dest>.previous. /Workspace cannot unlink notebooks, so delete through the API."""
    if os.path.exists(dest + ".previous"):
        try:
            shutil.rmtree(dest + ".previous")
        except OSError:
            _WC().workspace.delete(dest + ".previous", recursive=True)
        for _ in range(30):
            if not os.path.exists(dest + ".previous"):
                break
            time.sleep(1)
    if os.path.exists(dest):
        os.rename(dest, dest + ".previous")
        for _ in range(30):  # a renamed /Workspace path lingers for a few seconds
            if not os.path.exists(dest):
                break
            time.sleep(1)

INST = W["install_folder"].rstrip("/")
assert INST.startswith("/Workspace/"), "install_folder must be a /Workspace/... path"
replace_folder(INST)
os.makedirs(f"{INST}/cluster_wheels")
for (t, n), p in WHEEL.items():
    if t == "cluster":
        shutil.copyfile(p, f"{INST}/cluster_wheels/{n}")
shutil.copytree(f"{SRC}/runner_job", f"{INST}/runner_job")
n_cw = len(os.listdir(f"{INST}/cluster_wheels"))
assert n_cw == sum(t == "cluster" for t, _ in WHEEL), f"copied {n_cw} cluster wheels"
print(f"install folder {INST}: {n_cw} cluster wheels + runner notebook")
subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-index",
                       "--find-links", f"{INST}/cluster_wheels", "databricks-sdk==0.120.0", "databricks-labs-dqx==0.16.0"])
dbutils.library.restartPython()

# COMMAND ----------

import os, re, json, sys, time, shutil, hashlib
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
SRC = "/Workspace" + os.path.dirname(ctx.notebookPath().get())
PARENT = os.path.dirname(SRC)
me = json.loads(ctx.safeToJson())["attributes"]["user"]
CLUSTER_ID = json.loads(ctx.safeToJson())["attributes"].get("clusterId")
W = {k: dbutils.widgets.get(k).strip() for k in ["mode", "catalog_folder", "catalog_app_name", "standalone_app_name",
     "budget_policy_id", "catalog", "schema", "warehouse_id", "admin_group", "grant_job_run_to_users",
     "apply_schema_grants", "install_folder"]}
INST = W["install_folder"].rstrip("/")
APP_NAME = W["catalog_app_name"] if W["mode"] == "child" else W["standalone_app_name"]
JOB_NAME = f"{APP_NAME}-dqx-studio-task-runner" if W["mode"] == "child" else f"{APP_NAME}-task-runner"
man = json.load(open(f"{SRC}/wheels.json"))
N = man["packs"]
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.workspace import ImportFormat, Language
w = WorkspaceClient()
api = w.api_client
print("sdk", __import__("databricks.sdk.version", fromlist=["__version__"]).__version__, "as", w.current_user.me().user_name)

# COMMAND ----------

# MAGIC %md ## 3. Studio's tables in the existing schema (as you, through the SQL warehouse)

# COMMAND ----------

os.environ["DQX_SCHEMAS_PREEXIST"] = "1"
sys.path.insert(0, f"{SRC}/app/src")
from databricks_labs_dqx_app.backend.sql_executor import SqlExecutor
from databricks_labs_dqx_app.backend.migrations import MigrationRunner

sql = SqlExecutor(ws=w, warehouse_id=W["warehouse_id"], catalog=W["catalog"], schema=W["schema"])
applied = MigrationRunner(sql=sql).run_all(include_oltp_fallback=True)
TABLES = sorted(r[1] for r in sql.query(f"SHOW TABLES IN `{W['catalog']}`.`{W['schema']}`") if r[1].startswith("dq_"))
print(f"applied {applied} migration(s); Studio tables in {W['catalog']}.{W['schema']}: {len(TABLES)}")
print("(messages saying a migration was already applied are normal)")

# COMMAND ----------

# MAGIC %md ## 4. The task-runner job on this cluster, and who may run it

# COMMAND ----------

runner_nb = f"{INST}/runner_job/dqx_task_runner_job"
with open(f"{INST}/runner_job/dqx_task_runner_job.py.txt", "rb") as f:
    w.workspace.upload(runner_nb, f, format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=True)
params = {"task_type": "profile", "view_fqn": "", "result_catalog": W["catalog"], "result_schema": W["schema"],
          "config_json": "{}", "run_id": "", "requesting_user": "unknown", "warehouse_id": W["warehouse_id"],
          "wheelhouse": f"{INST}/cluster_wheels"}
settings = {"name": JOB_NAME, "max_concurrent_runs": 10, "timeout_seconds": 14400, "tags": {"app": APP_NAME},
            "parameters": [{"name": k, "default": v} for k, v in params.items()],
            "tasks": [{"task_key": "run_task", "existing_cluster_id": CLUSTER_ID,
                       "notebook_task": {"notebook_path": runner_nb, "source": "WORKSPACE"}}]}
existing = list(w.jobs.list(name=JOB_NAME))
if existing:
    JOB_ID = existing[0].job_id
    api.do("POST", "/api/2.2/jobs/reset", body={"job_id": JOB_ID, "new_settings": settings})
else:
    JOB_ID = api.do("POST", "/api/2.2/jobs/create", body=settings)["job_id"]
print("job", JOB_NAME, JOB_ID, "on cluster", CLUSTER_ID, "(runs as you, the job owner)")

if W["grant_job_run_to_users"] == "yes":
    api.do("PATCH", f"/api/2.0/permissions/jobs/{JOB_ID}",
           body={"access_control_list": [{"group_name": "users", "permission_level": "CAN_MANAGE_RUN"}]})
    acl = api.do("GET", f"/api/2.0/permissions/jobs/{JOB_ID}")
    ok = any(e.get("group_name") == "users" and any(p.get("permission_level") == "CAN_MANAGE_RUN" for p in e.get("all_permissions", []))
             for e in acl.get("access_control_list", []))
    print(f"CAN MANAGE RUN on job {JOB_ID} for group 'users' (every workspace user):", "granted" if ok else "NOT VISIBLE, check Jobs > Permissions")
else:
    print("Skipped the job grant (widget 10 = no): give each Studio user CAN MANAGE RUN on job", JOB_ID)

# COMMAND ----------

# MAGIC %md ## 5. What every user needs on Studio's schema
# MAGIC Studio reads and writes its tables as the signed-in user, and each run creates a temporary view in the schema.
# MAGIC These grants cover Studio's schema and tables only. Users also need SELECT on the tables they want to check.

# COMMAND ----------

C, S = f"`{W['catalog']}`", f"`{W['catalog']}`.`{W['schema']}`"
GRANTS = [f"GRANT USE CATALOG ON CATALOG {C} TO `account users`",
          f"GRANT USE SCHEMA, CREATE TABLE ON SCHEMA {S} TO `account users`"] + \
         [f"GRANT SELECT, MODIFY ON TABLE {S}.`{t}` TO `account users`" for t in TABLES]
print("Grants for all users (`account users`), Studio's schema only:\n")
for g in GRANTS:
    print("  " + g + ";")
print("\nWarehouse: SQL Warehouses > the warehouse > Permissions > add 'users' with 'Can use' (if they do not have it).")
if W["apply_schema_grants"] == "yes":
    print("\nApplying (each needs you to own, or hold MANAGE on, its object):")
    for g in GRANTS:
        try:
            sql.execute_no_schema(g)
            print("  OK     ", g)
        except Exception as e:
            print("  FAILED ", g, "->", str(e).split("\n")[0][:160])
    print("Ask the owner of the schema to run any FAILED line.")
else:
    print("\nNot applied (widget 11 = no). Set it to yes to apply the ones you own, or give the list to the schema owner.")

# COMMAND ----------

# MAGIC %md ## 6. Deploy the app

# COMMAND ----------

DEFAULT_SCOPES = {"iam.access-control:read", "iam.current-user:read"}
SCOPES = {"sql", "catalog.catalogs:read", "catalog.schemas:read", "catalog.tables:read"}
POLICY_ERR = "fallback polic"

def get_app(name):
    try:
        return api.do("GET", f"/api/2.0/apps/{name}")
    except NotFound:
        return None

def policy_candidates():
    ids = {}
    try:
        tok = None
        while True:
            r = api.do("GET", "/api/2.0/apps", query={"page_size": 100, **({"page_token": tok} if tok else {})})
            for a in r.get("apps", []):
                for k in ("budget_policy_id", "effective_budget_policy_id"):
                    if a.get(k):
                        ids.setdefault(a[k], set()).add(f"app {a['name']}")
            tok = r.get("next_page_token")
            if not tok:
                break
    except Exception as e:
        print("  (could not list apps:", str(e)[:100], ")")
    try:
        r = api.do("GET", "/api/2.1/jobs/list", query={"limit": 100})
        for j in r.get("jobs", []):
            for k in ("budget_policy_id", "effective_budget_policy_id"):
                v = j.get(k) or (j.get("settings") or {}).get(k)
                if v:
                    ids.setdefault(v, set()).add(f"job {(j.get('settings') or {}).get('name', j.get('job_id'))}")
    except Exception as e:
        print("  (could not list jobs:", str(e)[:100], ")")
    return ids

def keep_policy(a, body):
    """An app PATCH replaces the whole app: carry its policy over, or the update falls back and fails."""
    for k, eff in (("budget_policy_id", "effective_budget_policy_id"), ("usage_policy_id", "effective_usage_policy_id")):
        v = a.get(k) or a.get(eff)
        if v:
            body[k] = v
    return body

def create_app(name, description, scopes, policy_id):
    body = {"name": name, "description": description, "user_api_scopes": sorted(scopes)}
    if policy_id:
        body["budget_policy_id"] = policy_id
    try:
        api.do("POST", "/api/2.0/apps", body=body)
        return
    except Exception as e:
        if POLICY_ERR not in str(e) or policy_id:
            raise
        print("This workspace requires a budget policy on new apps. Looking for the ones in use …")
    cands = policy_candidates()
    for pid, users in cands.items():
        print(f"  {pid}   used by: {', '.join(sorted(users))[:120]}")
    if len(cands) == 1:
        pid = next(iter(cands))
        print("Exactly one policy is in use; using it:", pid)
        body["budget_policy_id"] = pid
        api.do("POST", "/api/2.0/apps", body=body)
        return
    raise RuntimeError(
        "Pick a budget policy: paste one of the ids above into widget 5 and Run all again, OR create the app in the UI "
        f"(Compute > Apps > Create app > Custom, name '{name}', choose the policy, add the user-authorization scopes "
        + ", ".join(sorted(scopes)) + ") and Run all again.")

def add_scopes(name, a, scopes):
    have = set(a.get("user_api_scopes") or []) - DEFAULT_SCOPES
    if scopes <= have:
        return True
    body = keep_policy(a, {"name": name, "description": a.get("description", ""),
                           "user_api_scopes": sorted(have | scopes), "resources": a.get("resources") or []})
    try:
        api.do("PATCH", f"/api/2.0/apps/{name}", body=body)
        print(f"{name}: scopes now {sorted(have | scopes)}")
        return True
    except Exception as e:
        print(f"Could not add the scopes {sorted(scopes - have)} to {name} ({str(e)[:140]}).\n"
              f"Add them in the UI: Compute > Apps > {name} > Edit > User authorization > + Add scope. If it says the "
              "scopes are not allowed, a workspace admin must enable user authorization for Databricks Apps.")
        return False

def wait_compute(name, states, minutes=15):
    st = None
    for _ in range(minutes * 12):
        a = get_app(name)
        st = (a.get("compute_status") or {}).get("state")
        if st in states:
            return a
        time.sleep(5)
    raise TimeoutError(f"compute of {name} did not reach {states}; last {st}")

def deploy(name, source):
    a = get_app(name)
    if (a.get("compute_status") or {}).get("state") != "ACTIVE":
        print("starting compute …")
        try:
            api.do("POST", f"/api/2.0/apps/{name}/start", body={})
        except Exception as e:
            print("  start:", str(e)[:120])
        wait_compute(name, {"ACTIVE"})
    print(f"deploying {source} → {name} …")
    dep = api.do("POST", f"/api/2.0/apps/{name}/deployments", body={"source_code_path": source, "mode": "SNAPSHOT"})
    t0 = time.time()
    while True:
        d = api.do("GET", f"/api/2.0/apps/{name}/deployments/{dep['deployment_id']}")
        st = (d.get("status") or {}).get("state")
        if st not in ("IN_PROGRESS", "PENDING", None):
            break
        if time.time() - t0 > 25 * 60:
            raise TimeoutError("deployment still running after 25 min: check Compute > Apps > Logs")
        time.sleep(10)
    print(f"deployment: {st} — {(d.get('status') or {}).get('message')} ({time.time() - t0:.0f}s)")
    assert st == "SUCCEEDED", "deployment failed: open Compute > Apps > this app > Logs"
    return get_app(name)

def write_app_folder(dest):
    """Studio's app folder: the code from this repo, the app wheels from the packs, and app.yaml with this
    workspace's values. No token: each user enters their own in the app."""
    shutil.copytree(f"{SRC}/app", dest)
    os.makedirs(f"{dest}/wheels", exist_ok=True)
    for f in man["files"]:
        if f["target"] == "app":
            shutil.copyfile(os.path.join(PARENT, f"dqx-studio-wheels-{f['pack']}-of-{N}", "app", f["name"])
                            if os.path.isdir(os.path.join(PARENT, f"dqx-studio-wheels-{f['pack']}-of-{N}"))
                            else os.path.join(SRC, f"dqx-studio-wheels-{f['pack']}-of-{N}", "app", f["name"]),
                            f"{dest}/wheels/{f['name']}")
    env = {"DQX_BROWSER_PAT": "1", "DQX_SCHEMAS_PREEXIST": "1", "DQX_SKIP_CATALOG_GRANT": "1",
           "DQX_SCHEDULER_DISABLED": "1", "DATABRICKS_WAREHOUSE_ID": W["warehouse_id"], "DQX_CATALOG": W["catalog"],
           "DQX_SCHEMA": W["schema"], "DQX_TMP_SCHEMA": W["schema"], "DQX_JOB_ID": str(JOB_ID), "DQX_JOB_NAME": JOB_NAME,
           "DQX_ADMIN_GROUP": W["admin_group"]}
    # Standalone: the PAT cookie's key is derived from this app's own secret, so tokens survive restarts. As a child
    # the key stays random and in memory only (siblings share App-ception's secret): users re-enter after a restart.
    if W["mode"] == "standalone":
        env["DQX_PAT_KEY_SCOPE"] = "app"
    app_yaml = {"command": ["python", "-m", "uvicorn", "databricks_labs_dqx_app.backend.app:app", "--app-dir", "src", "--workers", "1"],
                "env": [{"name": k, "value": v} for k, v in env.items()]}
    with open(f"{dest}/app.yaml", "w") as fh:
        json.dump(app_yaml, fh, indent=1)  # JSON is valid YAML
    n_app = len([x for x in os.listdir(f"{dest}/wheels") if x.endswith(".whl")])
    assert n_app == sum(f["target"] == "app" for f in man["files"]), f"copied {n_app} app wheels"
    print(f"app folder {dest}: code + {n_app} wheels + app.yaml")

if W["mode"] == "child":
    CAT = W["catalog_folder"].rstrip("/")
    assert CAT and os.path.isfile(f"{CAT}/catalog/server.mjs"), \
        f"widget 2 must be the App-ception folder (with catalog/server.mjs); got {CAT!r}. Import app-ception-main.zip first."
    a = get_app(APP_NAME)
    if a is None and APP_NAME == "app-ception" and get_app("app-catalog") is not None:
        APP_NAME, a = "app-catalog", get_app("app-catalog")   # deployed before the rename
        print("no app 'app-ception'; using the existing 'app-catalog' app")
    assert a is not None, f"no app {APP_NAME!r}: deploy App-ception first (its DEPLOY notebook), or fix widget 3"
    dest = f"{CAT}/child_apps/dqx_studio"
    if os.path.exists(dest):   # keep the previous copy OUTSIDE child_apps, so it is not discovered as an app
        bak = f"{CAT}/.bak-child-dqx_studio-{time.strftime('%Y%m%d-%H%M%S')}"
        os.rename(dest, bak)
        for _ in range(30):
            if not os.path.exists(dest):
                break
            time.sleep(1)
        print("previous copy kept at", bak)
    os.makedirs(dest)
    for item in ("catalog.yaml", "README.md", "LICENSES", "PATCHES.md"):
        p = f"{SRC}/{item}"
        (shutil.copytree if os.path.isdir(p) else shutil.copyfile)(p, f"{dest}/{item}")
    write_app_folder(f"{dest}/app")
    add_scopes(APP_NAME, wait_compute(APP_NAME, {"ACTIVE", "STOPPED", "ERROR"}), SCOPES)
    a = deploy(APP_NAME, CAT)
    url = a.get("url")
    print("\nCATALOG URL:", url)
    print("DQX STUDIO :", f"{url}/apps/dqx_studio/")
else:
    assert APP_NAME and APP_NAME == APP_NAME.lower(), "app name: lowercase letters, digits and '-'"
    APPDIR = f"{INST}/app"
    write_app_folder(APPDIR)
    if get_app(APP_NAME) is None:
        print(f"creating app {APP_NAME} …")
        create_app(APP_NAME, "DQX Studio (child and PAT edition)", SCOPES, W["budget_policy_id"])
    a = wait_compute(APP_NAME, {"ACTIVE", "STOPPED", "ERROR"})
    add_scopes(APP_NAME, a, SCOPES)
    a = deploy(APP_NAME, APPDIR)
    url = a.get("url")
    print("\nAPP URL:", url)
print("\nOpen it, enter your own PAT once (Settings > Developer > Access tokens), and use 'Check my access'.")
print("Scheduler is disabled in dynamic PAT mode.")
