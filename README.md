# DQX Studio for air-gapped workspaces: child and PAT edition

**This branch (`child-pat`) is the child and PAT edition.** It differs from the `main` build in three ways:
- it installs **standalone or as a child app of an App Catalog**;
- **each user enters their own personal access token (PAT)** in the app, kept only in their own browser, and no
  token is stored anywhere shared;
- the app **tells each user what access they are missing**, with the exact fix.

The `main` branch is the earlier single-token build and is unchanged.

DQX Studio is the no-code data-quality app from [Databricks Labs DQX](https://github.com/databrickslabs/dqx) (v0.16.0).
This build needs no internet (no PyPI, npm or GitHub), no CLI, no Lakebase, no serverless compute, no new catalogs,
schemas or volumes, and no grants to an app's service principal. **Not an official Databricks product**: DQX Studio
plus 9 patches, see [PATCHES.md](PATCHES.md).

## Install (browser only)

### What to download (7 files)
1. **This branch as a ZIP**: https://github.com/The-Real-Jeremy/dqx-studio-airgap/archive/refs/heads/child-pat.zip
   (`dqx-studio-airgap-child-pat.zip`, under 1 MiB).
2. **The 6 wheel packs** from the pre-release **child-pat-v1** (Releases on the GitHub page):
   `dqx-studio-wheels-1-of-6.zip` to `dqx-studio-wheels-6-of-6.zip`, each under 10 MiB. They hold the Python wheels
   Studio installs offline: 51 for the app, 30 for the cluster.

### Import them (the same way, 7 times)
In Databricks: **Workspace** → your home folder → **⋮ → Import** → drop **one** `.zip` → **Import**. Do this for
the branch ZIP, then for each pack, all into the **same** folder. Databricks unzips each into a folder of its own
name:

```
<your folder>/
  dqx-studio-airgap-child-pat/      ← the branch ZIP (has DEPLOY)
  dqx-studio-wheels-1-of-6/  …  dqx-studio-wheels-6-of-6/
  app-catalog-main/                 ← only for option B, imported from the App Catalog's own ZIP
```

### Before you run DEPLOY: what the workspace must give you
| You need | Notes |
|---|---|
| An existing **classic all-purpose cluster**, DBR **15.4 LTS** or **16.4 LTS**, x86_64 | DEPLOY runs on it, and so do Studio's jobs |
| An existing **catalog and schema** where you can create tables | Studio's 16 `dq_*` tables go here. It never creates a schema |
| An existing **SQL warehouse** you can use | |
| Permission to **create an app** (option A) or an **App Catalog** you deployed (option B) | |
| Every Studio user: their own **PAT** | They enter it in the app the first time (step 3) |

### A) Standalone: Studio as its own app
1. Open `dqx-studio-airgap-child-pat/DEPLOY` and attach it to the classic cluster.
2. Set the widgets: **1** `standalone`, **4** the app name (e.g. `dqx-studio`), **6** catalog, **7** schema,
   **8** SQL warehouse id (SQL Warehouses → the warehouse → its URL or *Connection details*). Leave the rest, then **Run all**.
   It takes about 10 to 15 minutes and prints the **APP URL**.
3. Open the URL and click **Authorize**. Studio asks for **your own PAT** (your user icon → Settings → Developer →
   Access tokens → Generate new token). Paste it, then press **Save and continue**.

If DEPLOY stops with **"Unable to use fallback policies"**, your workspace needs a budget policy on new apps.
DEPLOY prints the policy ids it can see, and uses the only one automatically. Otherwise, paste one into widget **5**
and Run all again. Or create the app in the UI first (**Compute → Apps → Create app → Custom**, the same name,
pick the policy) and Run all again: DEPLOY then only deploys.

### B) Child: Studio inside an App Catalog
1. Deploy the App Catalog first, from its own repo (`app-catalog-main/DEPLOY`, see its README).
2. Open `dqx-studio-airgap-child-pat/DEPLOY`, attach it to the classic cluster, set widget **1** to `child`.
   Widgets **2** (the catalog folder) and **3** (the catalog app name, default `app-catalog`) are filled in when
   `app-catalog-main/` sits next to this folder. Set **6**, **7** and **8** as in A, then **Run all**.
3. It copies Studio into `app-catalog-main/child_apps/dqx_studio/`, adds Studio's scopes to the catalog app, and
   redeploys the catalog. Open the catalog and click the **DQX Studio** card, then enter your PAT as in A.

### Updating to a newer version
Import onto an existing folder is refused ("already exists"). So first **rename or delete the old
`dqx-studio-airgap-child-pat/` and `dqx-studio-wheels-*` folders**. Then import the new ZIPs and run DEPLOY again in
the same mode. In option B, DEPLOY keeps the previous child copy as `app-catalog-main/.bak-child-dqx_studio-<time>`.
If you also update the App Catalog, run Studio's DEPLOY (mode `child`) again afterwards, because the new catalog
folder does not contain the old copy.

## What DEPLOY sets up, and who needs what

- **Studio's tables:** 16 `dq_*` Delta tables in your schema, created by you.
- **The task-runner job:** `<app>-task-runner` (standalone) or `<catalog app>-dqx-studio-task-runner` (child), on the
  cluster DEPLOY ran on. It runs as you (the job owner).
- **Widget 10** (`grant_job_run_to_users`, default **yes**): gives the workspace group **`users`** (every workspace
  user) **CAN MANAGE RUN** on that job, so anyone can start Studio's runs. It changes nothing else.
- **Widget 11** (`apply_schema_grants`, default **no**): DEPLOY always **prints** these grants. With `yes` it also
  applies the ones you are allowed to (you own the `dq_*` tables, so those succeed; the catalog and schema lines need
  their owner). They cover **Studio's schema only**:

  ```sql
  GRANT USE CATALOG ON CATALOG `<catalog>` TO `account users`;
  GRANT USE SCHEMA, CREATE TABLE ON SCHEMA `<catalog>`.`<schema>` TO `account users`;
  GRANT SELECT, MODIFY ON TABLE `<catalog>`.`<schema>`.`dq_<each of the 16>` TO `account users`;
  ```

  Why each is needed: users read and write Studio's tables as themselves (SELECT, MODIFY). Every run creates a
  temporary view in the schema (CREATE TABLE). Users also need CAN USE on the SQL warehouse, and SELECT on the
  tables they want to check.

## Using it

- **First visit**: a screen asks for your PAT. It is checked against the signed-in user (someone else's token is
  refused), then kept **only in your browser** as a secure cookie that page scripts cannot read. It is used only to
  start and watch DQX jobs. Everything else (browsing, rules, results) runs as you through the app's
  on-behalf-of sign-in. You are asked again only if you clear cookies, press **Forget token**, or the token expires
  (at most 30 days).
- **Check my access** (bottom-left bar) lists what you are missing, each with the exact fix: the job permission, the
  schema and table grants, the warehouse, and a missing or expired token. It also opens by itself on your first visit
  if something is missing, and whenever a request fails for lack of permission.
- **Scheduler is disabled in dynamic PAT mode**: no token is stored for the app, so nothing could start a run when
  nobody is using it. Run rules on demand from **Run Rules** (set **Row scope** to **All rows** to check every row).
- First-run check: **Create Rules → Single table rules**, table `samples.bakehouse.sales_transactions`, column
  `paymentMethod`, **Allowed values** `'visa', 'mastercard'` (**quote text values**: DQX 0.16 reads a bare word as a
  column name). Then **Drafts & Review → Submit → Approve**, **Run Rules → All rows → Run selected**. Expect
  **3,333 rows, 2,227 valid, 1,106 errors**.

## Troubleshooting
| What you see | Fix |
|---|---|
| DEPLOY: `Import these wheel packs into … first` | Import the named `dqx-studio-wheels-<k>-of-6.zip` into the same folder as this one |
| DEPLOY: `wheel(s) missing or corrupt` | Delete that pack's folder and import its ZIP again |
| DEPLOY: `attach this notebook to the existing classic all-purpose cluster` | DEPLOY must run on the classic cluster, not serverless |
| DEPLOY: `Unable to use fallback policies` | See the note under option A |
| DEPLOY: `Could not add the scopes` | Compute → Apps → the app → Edit → User authorization → add `sql`, `catalog.catalogs:read`, `catalog.schemas:read`, `catalog.tables:read`. If refused, a workspace admin must enable user authorization for Apps |
| App: `That token belongs to …` | Use a token you created yourself |
| App: a run fails with `UNRESOLVED_COLUMN` | Quote text values in Allowed values: `'visa'`, not `visa` |
| App: a run stays at "running" for minutes | The cluster was terminated and the job is starting it (about 6 to 8 minutes) |

## Remove it
1. Compute → Apps → the app → **Stop**, **Delete** (option A). In option B, delete `app-catalog-main/child_apps/dqx_studio/` and run the catalog's DEPLOY.
2. Jobs & Pipelines → the task-runner job → **Delete**.
3. Drop the 16 `dq_*` tables in your schema.
4. Delete the install folder (`/Workspace/Users/<you>/dqx_studio_install` and `.previous`), the imported folders, and revoke your PAT.

## Rebuild
`build/build_bundle.sh` clones DQX at `v0.16.0`, applies `build/patches/` (9) and builds the UI with the AI features
compiled out. `build/make_packs.py <app wheels> <cluster wheels>` splits the wheels into the packs and writes
`wheels.json`, which DEPLOY checks every file against (size and sha256).

## Licences
DQX and DQX Studio are © Databricks, Inc. and licensed under the **Databricks License**, which permits use only in
connection with the Databricks Services (copies in `LICENSES/`). This build modifies Studio: see
[PATCHES.md](PATCHES.md) and the `# MODIFIED` / `# ADDED` headers. The wheels in the packs are unmodified
third-party packages, each under its own licence (in its metadata), mostly Apache-2.0, MIT and BSD. The task-runner
wheel is built from DQX Studio's own `tasks/` source.
