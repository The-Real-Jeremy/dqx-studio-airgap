# Databricks notebook source
# MAGIC %md
# MAGIC # DQX Studio: air-gapped install
# MAGIC
# MAGIC Run this notebook on the **existing classic all-purpose cluster**, top to bottom, as the person who will own
# MAGIC the install. It needs no Databricks CLI, no PyPI, no Lakebase, no serverless jobs and **no grants to the app's
# MAGIC service principal**. It does the following:
# MAGIC
# MAGIC 1. Copies the `bundle/` folder of this repo (from the uploaded repo zip, or from the repo folder) into a workspace folder.
# MAGIC 2. Creates Studio's Delta tables in the **one existing schema**. It never runs `CREATE SCHEMA`.
# MAGIC 3. Creates or updates the task-runner job on the **existing classic cluster** (`existing_cluster_id`).
# MAGIC 4. Stores **your PAT**, created in the UI first (Settings > Developer > Access tokens; name it `dqx-studio-airgap`).
# MAGIC    The app uses it as its back-end identity in place of its service principal (POC workaround). A notebook
# MAGIC    cannot mint a PAT: `tokens.create` here returns the notebook's own command-context token.
# MAGIC 5. Creates the app (or reuses it), writes `app.yaml`, sets the OBO scopes, and deploys from the workspace folder.
# MAGIC
# MAGIC Re-running it is safe; it updates in place. Leave the `pat` widget empty on a re-run to keep the stored PAT.
# MAGIC To rotate, create a new PAT in the UI, paste it, re-run, then revoke the old one in the UI.

# COMMAND ----------

dbutils.widgets.text("bundle", "", "1. Repo zip or repo folder (blank = the folder this notebook is in)")
dbutils.widgets.text("catalog", "", "2. Catalog (existing)")
dbutils.widgets.text("schema", "", "3. Schema (existing; tables go here)")
dbutils.widgets.text("warehouse_id", "", "4. SQL warehouse id (existing)")
dbutils.widgets.text("cluster_id", "", "5. Classic cluster id (existing; blank = this one)")
dbutils.widgets.text("app_name", "dqx-studio-airgap", "6. App name")
dbutils.widgets.text("install_dir", "", "7. Workspace install folder (blank = ~/dqx_studio_airgap)")
dbutils.widgets.text("admin_group", "users", "8. Studio admin group")
dbutils.widgets.dropdown("pat_storage", "secret", ["secret", "app_yaml"], "9. Where the PAT is kept")
dbutils.widgets.text("secret_scope", "dqx_studio_airgap", "10. Secret scope (pat_storage=secret)")
dbutils.widgets.text("pat", "", "11. PAT (paste; blank = keep stored)")

# COMMAND ----------

import json, os, zipfile, tempfile, shutil, time

W = {k: dbutils.widgets.get(k).strip() for k in [
    "bundle", "catalog", "schema", "warehouse_id", "cluster_id", "app_name", "install_dir",
    "admin_group", "pat_storage", "secret_scope"]}
ctx = json.loads(dbutils.notebook.entry_point.getDbutils().notebook().getContext().safeToJson())["attributes"]
me = ctx["user"]
W["cluster_id"] = W["cluster_id"] or ctx["clusterId"]
W["install_dir"] = (W["install_dir"] or f"/Workspace/Users/{me}/dqx_studio_airgap").rstrip("/")
if not W["bundle"]:  # the repo folder this notebook sits in (Git folder or uploaded folder)
    nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    W["bundle"] = os.path.dirname(nb_path if nb_path.startswith("/Workspace") else "/Workspace" + nb_path)
for k in ("bundle", "catalog", "schema", "warehouse_id"):
    assert W[k] and "<" not in W[k], f"widget '{k}' must be set"
assert W["install_dir"].startswith("/Workspace/"), "install_dir must be a /Workspace/... path"
print(json.dumps({**W, "installer": me}, indent=1))

# COMMAND ----------

# MAGIC %md ## 1. Unpack the bundle into the workspace folder

# COMMAND ----------

src = W["bundle"].rstrip("/")
if src.lower().endswith(".zip"):  # GitHub "Download ZIP", or the repo folder zipped on the laptop
    stage = tempfile.mkdtemp(prefix="dqx_studio_")
    local_zip = os.path.join(stage, "repo.zip")
    shutil.copyfile(src, local_zip)  # /Volumes/... or /Workspace/... path
    with zipfile.ZipFile(local_zip) as z:
        z.extractall(stage)
    src = stage
# The deployable tree is the repo's bundle/ folder (the one holding MANIFEST.json), at any depth.
found = [d for d, _, fs in os.walk(src) if "MANIFEST.json" in fs and os.path.basename(d) == "bundle"]
assert len(found) == 1, f"expected one bundle/MANIFEST.json under {src}, found {found or 'none'}: check widget 1"
root = found[0]
manifest = json.load(open(os.path.join(root, "MANIFEST.json")))
print("bundle", manifest["bundle_version"], "built", manifest["built_at"], "files", manifest["file_count"], "from", root)

# Count what arrived against the manifest (a partial copy must fail here, not at runtime).
arrived = sum(len(fs) for _, _, fs in os.walk(root))
assert arrived == manifest["file_count"], f"bundle has {arrived} files, manifest says {manifest['file_count']}"

# The cluster wheelhouse goes to the workspace too: the runner job installs from it on every run.
dest = W["install_dir"]
if os.path.exists(dest):
    # Keep the previous install as <install_dir>.previous (one generation) instead of deleting it.
    if os.path.exists(dest + ".previous"):
        try:
            shutil.rmtree(dest + ".previous")
        except OSError:  # /Workspace cannot unlink notebooks (Errno 95): delete through the Workspace API
            from databricks.sdk import WorkspaceClient
            WorkspaceClient().workspace.delete(dest + ".previous", recursive=True)
            for _ in range(30):
                if not os.path.exists(dest + ".previous"):
                    break
                time.sleep(1)
    os.rename(dest, dest + ".previous")
    for _ in range(30):  # the /Workspace mount can report the old path for a few seconds after a rename
        if not os.path.exists(dest):
            break
        time.sleep(1)
shutil.copytree(root, dest, dirs_exist_ok=True)
copied = sum(len(fs) for _, _, fs in os.walk(dest))
assert copied == manifest["file_count"], f"copied {copied} of {manifest['file_count']} files to {dest}"
print(f"unpacked {copied} files into {dest}")

# COMMAND ----------

# MAGIC %md ## 2. Install the SDK + DQX for this notebook, offline (no PyPI)

# COMMAND ----------

import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                       "--no-index", "--find-links", f"{W['install_dir']}/cluster_wheels",
                       "databricks-sdk==0.120.0", "databricks-labs-dqx==0.16.0"])
dbutils.library.restartPython()

# COMMAND ----------

import json, os, sys, time
W = {k: dbutils.widgets.get(k).strip() for k in [
    "bundle", "catalog", "schema", "warehouse_id", "cluster_id", "app_name", "install_dir",
    "admin_group", "pat_storage", "secret_scope"]}
ctx = json.loads(dbutils.notebook.entry_point.getDbutils().notebook().getContext().safeToJson())["attributes"]
me = ctx["user"]
W["cluster_id"] = W["cluster_id"] or ctx["clusterId"]
W["install_dir"] = (W["install_dir"] or f"/Workspace/Users/{me}/dqx_studio_airgap").rstrip("/")
APP_DIR = f"{W['install_dir']}/app"

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ImportFormat, Language
w = WorkspaceClient()
assert w.current_user.me().user_name == me
print("sdk", __import__("databricks.sdk.version", fromlist=["__version__"]).__version__, "as", me)

# COMMAND ----------

# MAGIC %md ## 3. Create Studio's Delta tables in the existing schema
# MAGIC Uses Studio's own migration runner (the same DDL the app runs at start-up) through the SQL warehouse, as you.
# MAGIC With `DQX_SCHEMAS_PREEXIST=1` it skips `CREATE SCHEMA`, so the only DDL is `CREATE TABLE` and `ALTER TABLE`.

# COMMAND ----------

os.environ["DQX_SCHEMAS_PREEXIST"] = "1"
sys.path.insert(0, f"{APP_DIR}/src")
from databricks_labs_dqx_app.backend.sql_executor import SqlExecutor
from databricks_labs_dqx_app.backend.migrations import MigrationRunner

sql = SqlExecutor(ws=w, warehouse_id=W["warehouse_id"], catalog=W["catalog"], schema=W["schema"])
applied = MigrationRunner(sql=sql).run_all(include_oltp_fallback=True)  # OLTP tables live in Delta (no Lakebase)
tables = [r[1] for r in sql.query(f"SHOW TABLES IN `{W['catalog']}`.`{W['schema']}`") if r[1].startswith("dq_")]
print(f"applied {applied} migration(s); dq_ tables now in {W['catalog']}.{W['schema']}: {len(tables)}")
print(sorted(tables))

# COMMAND ----------

# MAGIC %md ## 4. Task-runner job on the existing classic cluster

# COMMAND ----------

runner_nb = f"{W['install_dir']}/runner_job/dqx_task_runner_job"
with open(f"{W['install_dir']}/runner_job/dqx_task_runner_job.py.txt", "rb") as f:
    w.workspace.upload(runner_nb, f, format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=True)

job_name = f"{W['app_name']}-task-runner"
params = {"task_type": "profile", "view_fqn": "", "result_catalog": W["catalog"], "result_schema": W["schema"],
          "config_json": "{}", "run_id": "", "requesting_user": "unknown", "warehouse_id": W["warehouse_id"],
          "wheelhouse": f"{W['install_dir']}/cluster_wheels"}
settings = {
    "name": job_name,
    "max_concurrent_runs": 10,
    "timeout_seconds": 14400,
    "tags": {"app": W["app_name"]},
    "parameters": [{"name": k, "default": v} for k, v in params.items()],
    "tasks": [{"task_key": "run_task", "existing_cluster_id": W["cluster_id"],
               "notebook_task": {"notebook_path": runner_nb, "source": "WORKSPACE"}}],
}
existing = [j for j in w.jobs.list(name=job_name)]
if existing:
    job_id = existing[0].job_id
    w.api_client.do("POST", "/api/2.2/jobs/reset", body={"job_id": job_id, "new_settings": settings})
else:
    job_id = w.api_client.do("POST", "/api/2.2/jobs/create", body=settings)["job_id"]
print("job", job_name, job_id, "on cluster", W["cluster_id"])

# COMMAND ----------

# MAGIC %md ## 5. Your PAT as the app's back-end identity (POC workaround for "no grants to the app SP")

# COMMAND ----------

import requests
host = "https://" + spark.conf.get("spark.databricks.workspaceUrl")
ctx_token = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
pat = dbutils.widgets.get("pat").strip()
have_secret = (W["pat_storage"] == "secret" and W["secret_scope"] in [s.name for s in w.secrets.list_scopes()]
               and "pat" in [k.key for k in w.secrets.list_secrets(scope=W["secret_scope"])])
prev_yaml = f"{W['install_dir']}.previous/app/app.yaml"
if not pat and W["pat_storage"] == "app_yaml" and os.path.exists(prev_yaml):
    pat = next(e["value"] for e in json.load(open(prev_yaml))["env"] if e["name"] == "DATABRICKS_TOKEN" and "value" in e)
    print("keeping the PAT from the previous install's app.yaml")
elif not pat:
    assert have_secret, "paste a PAT into the 'pat' widget (Settings > Developer > Access tokens), then re-run"
    print(f"keeping the PAT already stored in secret {W['secret_scope']}/pat")
else:
    assert pat != ctx_token, "that is this notebook's own context token, not a PAT you created"
    who = requests.get(f"{host}/api/2.0/preview/scim/v2/Me", headers={"Authorization": f"Bearer {pat}"}, timeout=30)
    assert who.ok, f"the PAT was rejected ({who.status_code}): PATs may be disabled for you in this workspace"
    assert who.json().get("userName") == me, f"the PAT belongs to {who.json().get('userName')}, not {me}"
    if W["pat_storage"] == "secret":
        if W["secret_scope"] not in [s.name for s in w.secrets.list_scopes()]:
            w.secrets.create_scope(scope=W["secret_scope"])
        w.secrets.put_secret(scope=W["secret_scope"], key="pat", string_value=pat)
    print(f"PAT for {me} validated and stored in {W['pat_storage']}")
# Expiry of your PATs, read from the token list (it never returns token values).
for t in w.tokens.list():
    if (t.comment or "").lower().startswith("dqx-studio"):
        exp = "never" if (t.expiry_time or -1) < 0 else time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t.expiry_time / 1000))
        print(f"PAT '{t.comment}' expires {exp}: the app stops working then; paste a new PAT and re-run before that")

# COMMAND ----------

# MAGIC %md ## 6. Create the app, write `app.yaml`, set scopes, deploy

# COMMAND ----------

name = W["app_name"]
try:
    app = w.api_client.do("GET", f"/api/2.0/apps/{name}")
except Exception:
    app = w.api_client.do("POST", "/api/2.0/apps", body={"name": name, "description": "DQX Studio (air-gapped build)"})
for _ in range(80):
    app = w.api_client.do("GET", f"/api/2.0/apps/{name}")
    if app.get("compute_status", {}).get("state") in ("ACTIVE", "ERROR", "STOPPED"):
        break
    time.sleep(10)
if app.get("compute_status", {}).get("state") == "STOPPED":
    w.api_client.do("POST", f"/api/2.0/apps/{name}/start")
print("app", name, app.get("compute_status"), app.get("url"))

env = [
    {"name": "DATABRICKS_AUTH_TYPE", "value": "pat"},  # SDK uses the PAT, not the injected SP credentials
    ({"name": "DATABRICKS_TOKEN", "valueFrom": "dqx-pat"} if W["pat_storage"] == "secret"
     else {"name": "DATABRICKS_TOKEN", "value": pat}),
    {"name": "DATABRICKS_WAREHOUSE_ID", "value": W["warehouse_id"]},
    {"name": "DQX_CATALOG", "value": W["catalog"]},
    {"name": "DQX_SCHEMA", "value": W["schema"]},
    {"name": "DQX_TMP_SCHEMA", "value": W["schema"]},  # one schema only: temp views live beside the tables
    {"name": "DQX_SCHEMAS_PREEXIST", "value": "1"},  # never CREATE SCHEMA
    {"name": "DQX_SKIP_CATALOG_GRANT", "value": "1"},  # never GRANT on the catalog
    {"name": "DQX_JOB_ID", "value": str(job_id)},
    {"name": "DQX_ADMIN_GROUP", "value": W["admin_group"]},
]
app_yaml = {"command": ["python", "-m", "uvicorn", "databricks_labs_dqx_app.backend.app:app",
                        "--app-dir", "src", "--workers", "1"], "env": env}
with open(f"{APP_DIR}/app.yaml", "w") as f:
    json.dump(app_yaml, f, indent=1)  # JSON is valid YAML
del pat
try:
    dbutils.widgets.remove("pat")  # do not leave the PAT sitting in the widget
except Exception:
    pass

resources = []
if W["pat_storage"] == "secret":
    resources.append({"name": "dqx-pat", "description": "PAT used as the app back-end identity",
                      "secret": {"scope": W["secret_scope"], "key": "pat", "permission": "READ"}})
w.api_client.do("PATCH", f"/api/2.0/apps/{name}", body={
    "description": "DQX Studio (air-gapped build)",
    "user_api_scopes": ["sql", "catalog.catalogs:read", "catalog.schemas:read", "catalog.tables:read"],
    "resources": resources,
})

dep = w.api_client.do("POST", f"/api/2.0/apps/{name}/deployments", body={"source_code_path": APP_DIR, "mode": "SNAPSHOT"})
for _ in range(90):
    dep = w.api_client.do("GET", f"/api/2.0/apps/{name}/deployments/{dep['deployment_id']}")
    if dep["status"]["state"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
        break
    time.sleep(10)
app = w.api_client.do("GET", f"/api/2.0/apps/{name}")
print("deployment", dep["status"])
print("app status", app.get("app_status"))
print("effective OBO scopes", app.get("effective_user_api_scopes"))
assert dep["status"]["state"] == "SUCCEEDED", "deployment failed: open the app's Logs tab"
print("\nOPEN:", app["url"])

# COMMAND ----------

# MAGIC %md ## 7. Evidence: nothing was granted to the app's service principal in Unity Catalog

# COMMAND ----------

sp = app["service_principal_client_id"]
grants = sql.query(f"SHOW GRANTS ON SCHEMA `{W['catalog']}`.`{W['schema']}`")
sp_grants = [g for g in grants if g[0] == sp]
print("app SP", sp, "UC grants on the schema:", sp_grants or "none")
print("app resources:", [r["name"] for r in app.get("resources", [])] or "none")
