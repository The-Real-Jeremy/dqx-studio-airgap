# DQX Studio for air-gapped workspaces

An installable build of **DQX Studio**, the no-code data-quality app from [Databricks Labs DQX](https://github.com/databrickslabs/dqx)
(v0.16.0), for a Databricks workspace that has:

- no internet (no PyPI, npm or GitHub) at install or run time, and no Databricks CLI;
- no Lakebase, no serverless jobs and no serverless all-purpose compute;
- no permission to create catalogs, schemas or volumes, or to grant anything to an app's service principal;
- no Foundation Model APIs (the AI features are switched off).

Everything installs from **one setup notebook** on an existing classic cluster. Studio then runs every Spark step as
a job on that cluster and keeps all its state in Delta tables in **one existing schema**.

**This is not an official Databricks product.** It is DQX Studio with 6 small patches (see [PATCHES.md](PATCHES.md)),
built for a proof of concept. The back end runs as **your personal access token (PAT)** in place of the app's service
principal; read [PAT exposure](#pat-exposure) before using it.

## What is in this repo

| Path | What it is | Size |
|---|---|---|
| `setup_notebook.py` | The only notebook you run. Installs everything | 15 KB |
| `bundle/app/` | Studio's back end and prebuilt UI, plus 54 vendored wheels for the Apps runtime (Linux x86_64, CPython 3.11) | 113 MB |
| `bundle/cluster_wheels/` | DQX 0.16.0, the Databricks SDK and the task-runner wheel for the cluster (CPython 3.11 and 3.12, x86_64 and aarch64) | 42 MB |
| `bundle/runner_job/` | The notebook the task-runner job runs | 3 KB |
| `bundle/MANIFEST.json` | Version and file count (the setup checks it) | |
| `build/` | How `bundle/` was built: `build_bundle.sh`, the patches, the pinned requirements | |
| `PATCHES.md`, `LICENSES/` | Changes against upstream, and the licences | |
| `SHA256SUMS.txt` | A checksum for every file in `bundle/` and the setup notebook | |

## Before you start: what the workspace must give you

| You need | Notes |
|---|---|
| An existing **classic all-purpose cluster** on **DBR 15.4 LTS or 16.4 LTS**, x86_64 or Graviton, standard or dedicated access mode | You need CAN ATTACH TO. CAN RESTART lets jobs start it when it is terminated |
| An existing **schema** where you have USE SCHEMA, CREATE TABLE, SELECT and MODIFY (plus USE CATALOG on its catalog) | Studio creates its 16 tables here. Creating a new schema is not needed |
| An existing **volume** where you have READ VOLUME and WRITE VOLUME | Only to upload the repo zip. Creating a volume needs CREATE VOLUME, which you may not have; ask the schema owner for one, or use a Git folder (step 3, option B) |
| An existing **SQL warehouse** you can use (CAN USE) | Studio reads and writes its tables through it |
| Permission to **create a Databricks App** and a **job** | |
| Permission to **create a PAT** | If PATs are disabled for you, this build cannot be used |

The cluster wheels cover Python 3.11 (DBR 15.4 LTS) and 3.12 (DBR 16.4 LTS). Another DBR needs a rebuild with its Python version. The app side does not depend on the cluster.

## Install: step by step

### 1. Get this repo onto your laptop

Either **Code > Download ZIP** on GitHub (you get `dqx-studio-airgap-main.zip`, about 150 MB), or `git clone` it and then
zip the folder (on a Mac: right-click the folder > **Compress**). You will upload that single zip.

### 2. Create a PAT

In the workspace: your user icon (top right) > **Settings** > **Developer** > **Access tokens** > **Manage** > **Generate new token**.

- Comment: `dqx-studio-airgap` (the setup notebook reports the expiry of tokens whose comment starts with `dqx-studio`).
- Lifetime: the longest your workspace allows. **Write down the expiry date.** When the PAT expires the app stops working until you re-run the setup with a new one (step 9).
- Copy the token now; it is shown only once.

### 3. Put the repo in the workspace

**Option A: upload the zip to a volume (the usual way).**
Catalog Explorer > your catalog > your schema > **Volumes** > the volume > **Upload to this volume** > pick the zip.
Then import the notebook on its own: **Workspace** > your folder > **⋮** > **Import** > **File** > `setup_notebook.py` (from the zip or the cloned folder).

**Option B: a Git folder**, only if the workspace can reach a Git server that hosts this repo.
**Workspace** > **Create** > **Git folder** > paste the repo URL. The setup notebook is then already inside the repo folder.

### 4. Open the setup notebook and attach it to the cluster

Open `setup_notebook` and attach it to the existing classic cluster (the cluster selector at the top right).

### 5. Fill the settings

The first cell creates these fields at the top of the notebook. Run that cell once (Shift+Enter) to make them appear, then fill them in:

| # | Field | What to enter | Where to find it |
|---|---|---|---|
| 1 | Repo zip or repo folder | Option A: the zip's path, e.g. `/Volumes/<catalog>/<schema>/<volume>/dqx-studio-airgap-main.zip`. Option B: leave blank | Catalog Explorer > the volume > the file > **Copy path** |
| 2 | Catalog | The existing catalog | Catalog Explorer |
| 3 | Schema | The existing schema (tables are created here) | Catalog Explorer |
| 4 | SQL warehouse id | e.g. `1234567890abcdef` | **SQL Warehouses** > the warehouse > **Connection details**, or the id in its URL |
| 5 | Classic cluster id | Leave blank to use the cluster the notebook is attached to | **Compute** > the cluster > the id in its URL |
| 6 | App name | Lowercase letters, numbers and hyphens, e.g. `dqx-studio-airgap` | Your choice; must not clash with an existing app |
| 7 | Workspace install folder | Leave blank for `/Workspace/Users/<you>/dqx_studio_airgap` | |
| 8 | Studio admin group | A group you are in. `users` makes every user a Studio admin (fine for a POC) | **Settings** > **Identity and access** > **Groups** |
| 9 | Where the PAT is kept | `secret` (a secret scope, the default) or `app_yaml` (inside the app's config) | See [PAT exposure](#pat-exposure) |
| 10 | Secret scope | e.g. `dqx_studio_airgap`. Created if it does not exist | |
| 11 | PAT | Paste the token from step 2. Leave blank on a re-run to keep the stored one | |

### 6. Run all

**Run all** (top of the notebook). It takes about 3 minutes. In order, it:

1. copies `bundle/` into the install folder and checks the file count;
2. installs DQX for the notebook offline;
3. creates Studio's 16 Delta tables in your schema;
4. creates the job `<app name>-task-runner` on the existing cluster;
5. checks that the PAT is yours and stores it;
6. creates and deploys the app.

The last cells print **OPEN: https://...** (the app URL), and confirm that the app's service principal has no Unity
Catalog grants.

### 7. Open the app

Open the URL. On the first visit click **Authorize** on the consent screen.

### 8. First-run check (about 5 minutes)

1. **Create Rules** > **Single table rules**. Pick a table you can read, e.g. `samples.bakehouse.sales_transactions` if the
   `samples` catalog is available.
2. Add a rule: column `paymentMethod`, check **Allowed values**, values `'visa', 'mastercard'`, criticality **error**.
   **Quote text values.** DQX 0.16 reads an unquoted word as a column name, and the run then fails with `UNRESOLVED_COLUMN`.
3. Save, then **Drafts & Review** > **Submit**, then **Approve**.
4. **Run Rules** > select the table > **Run selected**. With the cluster running this takes about a minute.
5. **Runs History** > the run. For `samples.bakehouse.sales_transactions` you should see **3,333 rows, 2,227 valid, 1,106 errors**
   (every `amex` sale fails the rule).

### 9. Later: re-run, rotate the PAT, upgrade

- **Re-run** the notebook any time; it updates in place and keeps the previous install as `<install folder>.previous`.
- **Rotate the PAT:** create a new one (step 2), paste it into field 11, run all, then revoke the old one.

## How long a run takes

| Cluster state | Time per run |
|---|---|
| Running | about 45 to 80 seconds (most of it is the offline `pip install` each run does) |
| Terminated | about 8 minutes: the job starts the cluster first (about 6 minutes) |

Schedules start the cluster when they fire, so the cluster's auto-termination setting decides most of the cost.

## PAT exposure

- The PAT carries **your whole identity**. Every back-end action runs as you: saving rules, triggering jobs, and scheduled
  runs, which read source tables directly as you. Interactive dry runs still check the **viewer's** own access.
- With `secret`, anyone with READ on the secret scope can read it: you, workspace admins, and the app's service principal
  (the setup adds that one READ ACL on the scope; it is not a Unity Catalog grant). With `app_yaml` nothing is granted, but
  anyone who can read the install folder or the app's deployed source can read it.
- Revoke it when you finish the POC (Settings > Developer > Access tokens).

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `widget 'catalog' must be set` (or another field) | A field in step 5 is empty | Fill it in and run all again |
| `expected one bundle/MANIFEST.json under ...` | Field 1 does not point at the repo zip or folder | Use **Copy path** on the uploaded zip; for a Git folder leave it blank |
| `the PAT was rejected (403)` or `(401)` | PATs are disabled for you, or the token was mistyped or has expired | Create a new PAT (step 2). If PATs are disabled, this build cannot be used |
| `that is this notebook's own context token` | A token was taken from inside a notebook | Only a PAT created in the UI (step 2) works |
| A run fails with `UNRESOLVED_COLUMN` | An allowed value is not quoted | Edit the rule and write `'visa'`, not `visa` |
| A run sits at "running" for several minutes | The cluster was terminated and the job is starting it | Wait about 8 minutes, or start the cluster first |
| `deployment failed: open the app's Logs tab` | The app did not start | **Compute** > **Apps** > the app > **Logs**. Then re-run the notebook |
| `PERMISSION_DENIED` creating tables | Missing CREATE TABLE or USE SCHEMA on the schema | Ask the schema owner for those privileges |

## Remove it

1. **Compute** > **Apps** > the app > **Stop**, then **Delete**.
2. **Jobs & Pipelines** > `<app name>-task-runner` > **Delete**.
3. Drop Studio's tables: in a notebook or the SQL editor, `SHOW TABLES IN <catalog>.<schema> LIKE 'dq_*'`, then `DROP TABLE` each one. Also drop any leftover `tmp_view_*` views.
4. Delete the secret scope (if you used `secret`): in a notebook, `dbutils.secrets` cannot delete scopes, so use
   `from databricks.sdk import WorkspaceClient; WorkspaceClient().secrets.delete_scope("<scope>")`.
5. Delete the install folder (`/Workspace/Users/<you>/dqx_studio_airgap` and `.previous`) and the uploaded zip.
6. Revoke the PAT.

## Rebuild `bundle/` (machine with internet)

`build/build_bundle.sh` clones DQX at `v0.16.0` and applies `build/patches/`. It then builds the UI with the AI and
contract features compiled out, downloads every wheel, and checks that both wheel sets install offline. Finally it replaces
`bundle/` and writes `SHA256SUMS.txt`. It needs git, node and yarn 1.x, uv and python3.11.

## What changes for production

Use stock DQX Studio (`studio-v0.1.0` or later) with:

- a **Lakebase** project for its state (required from `studio-v0.1.0`);
- a properly permissioned app service principal and task-runner service principal in place of the PAT;
- serverless jobs (or a bundle override to a classic cluster);
- a package mirror reachable from the Apps builder;
- FMAPI for the AI features.

## Licences

DQX and DQX Studio are © Databricks, Inc. and licensed under the **Databricks License**. It permits use only in connection
with the Databricks Services. A copy of the licence and the NOTICE are in `LICENSES/`. This build modifies Studio:
see [PATCHES.md](PATCHES.md) and the `# MODIFIED` headers. The vendored wheels in `bundle/app/wheels/` and
`bundle/cluster_wheels/` are unmodified third-party packages, each under its own licence (in its metadata), mostly
Apache-2.0, MIT and BSD. The task-runner wheel is built from DQX Studio's own `tasks/` source.
