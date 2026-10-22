# Databricks notebook source
# MAGIC %md
# MAGIC # DQX Studio task runner (Air-gap build)
# MAGIC
# MAGIC The Studio app triggers this notebook through its task-runner job (`DQX_JOB_ID`). The job runs on the
# MAGIC **existing classic all-purpose cluster** (`existing_cluster_id`) instead of upstream's serverless
# MAGIC environment. The notebook installs DQX **offline** from the wheelhouse folder (`--no-index`) and then
# MAGIC calls the **unchanged** upstream runner (`dqx_task_runner.runner.main`), passing it the job parameters
# MAGIC the app sends.

# COMMAND ----------

import subprocess
import sys

dbutils.widgets.text("wheelhouse", "")
wheelhouse = dbutils.widgets.get("wheelhouse").rstrip("/")
if not wheelhouse:
    raise ValueError("job parameter 'wheelhouse' is empty: set it to the cluster_wheels folder of the install")

# Notebook-scoped install. --no-index means pip never contacts a package index, so the run
# behaves the same in an air-gapped workspace. Packages DBR already provides at a satisfying
# version are kept; only the missing ones come from the wheelhouse.
cmd = [
    sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
    "--no-index", "--find-links", wheelhouse,
    "databricks-labs-dqx==0.16.0", "databricks-sdk==0.120.0", "databricks-labs-dqx-task-runner==0.1.0",
]
print(" ".join(cmd))
proc = subprocess.run(cmd, capture_output=True, text=True)
print(proc.stdout[-4000:])
if proc.returncode != 0:
    raise RuntimeError(f"offline pip install failed (exit {proc.returncode}):\n{proc.stderr[-4000:]}")

# COMMAND ----------

# Restart Python so the upgraded databricks-sdk is imported instead of the copy DBR already loaded.
dbutils.library.restartPython()

# COMMAND ----------

import sys

from databricks.labs.dqx.__about__ import __version__ as dqx_version
from dqx_task_runner import runner

# Same arguments the upstream python_wheel_task receives (see runner._parse_args).
ARGS = ["task_type", "view_fqn", "result_catalog", "result_schema", "config_json", "run_id", "requesting_user", "warehouse_id"]
for name in ARGS:
    dbutils.widgets.text(name, "")
argv = ["dqx-task-runner"]
for name in ARGS:
    value = dbutils.widgets.get(name)
    if value != "" or name not in ("requesting_user", "warehouse_id"):
        argv += [f"--{name}", value]

print(f"databricks-labs-dqx {dqx_version}; task_type={dbutils.widgets.get('task_type')} run_id={dbutils.widgets.get('run_id')}")
sys.argv = argv
try:
    runner.main()
except SystemExit as exc:
    # The runner calls sys.exit(1) after it has recorded the failure in the results tables;
    # re-raise as an error so the job run is marked FAILED (the app polls that state).
    if exc.code not in (None, 0):
        raise RuntimeError(f"DQX task runner exited with code {exc.code}") from None
