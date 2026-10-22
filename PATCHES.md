# Changes against upstream DQX v0.16.0

This build is **modified** from the DQX Studio app in [databrickslabs/dqx](https://github.com/databrickslabs/dqx) at tag
`v0.16.0` (commit `16031b4`). The patches are in `build/patches/` and apply with `git am`. They change 14 files.

The 4 modified Python files in `bundle/app/src/` carry a `# MODIFIED` header. The modified TypeScript, JSON and
`tasks/pyproject.toml` files are not shipped as source: they are compiled into `bundle/app/src/databricks_labs_dqx_app/__dist__/`
(the UI) and into `bundle/cluster_wheels/databricks_labs_dqx_task_runner-0.1.0-py3-none-any.whl` (the task runner).

| # | Files | Change | Why |
|---|---|---|---|
| 0001 | `backend/sql_executor.py`; `ui/lib/features.ts`, `ui/components/AIAssistantProvider.tsx`, `ui/routes/_sidebar/profiler.tsx`, `rules.create-sql.tsx`, `rules.import.tsx`, `rules.single-table.tsx` | `DQX_SCHEMAS_PREEXIST=1` skips every `CREATE SCHEMA IF NOT EXISTS`. Build flags remove the AI assistant, the AI rule-generation cards, the LLM primary-key toggle and the data-contract import tab | The installer cannot create schemas, and there is no FMAPI |
| 0002 | `backend/services/ai_rules_service.py` | Guarded import of `databricks_langchain`, `langchain_core` and `dqx.llm` | Without the `[llm]` extra the app failed to import |
| 0003 | `backend/sql_executor.py`, `ui/routes/_sidebar/home.tsx`, `tasks/pyproject.toml` | The task runner pins `databricks-labs-dqx==0.16.0`. `DQX_SKIP_CATALOG_GRANT=1` skips the start-up `GRANT USE CATALOG ... TO account users`. The home page "AI-powered" chip follows the AI flag | Version conflict on the cluster; the install must not change catalog permissions |
| 0004 | `backend/sql_utils.py`, `backend/pg_executor.py` | `escape_sql_string` uses backslash escaping for Databricks SQL (Postgres keeps `''`) | Upstream bug: Databricks SQL reads `'a''b'` as two adjacent literals (`ab`), so every single quote Studio wrote to Delta was silently dropped |
| 0005 | `ui/lib/i18n/locales/en.json` | The allowed-values hint says to quote text values | DQX 0.16 reads a bare string in `is_in_list` as a column name |
| 0006 | `ui/lib/format-utils.ts` | The UI reads zone-less API timestamps as UTC | On the Delta backend the times showed in the wrong time zone |
| 0007 | `tasks/pyproject.toml` | The task runner declares `requires-python >=3.11` (was `>=3.12`) | So the runner job also works on DBR 15.4 LTS (Python 3.11). The runner code has no 3.12-only syntax |

With the flags unset, 0001 and 0003 behave exactly like upstream.

Other files written by this repository (not modified upstream files): `setup_notebook.py`,
`bundle/runner_job/dqx_task_runner_job.py.txt` (runs the unchanged upstream runner on a classic cluster after an
offline `pip install`), `bundle/app/requirements.txt`, `bundle/MANIFEST.json`, `build/`, and the documentation.
The build also rewrites `_metadata.py` and `_version.py` (`0.15.0+airgap.1`).
