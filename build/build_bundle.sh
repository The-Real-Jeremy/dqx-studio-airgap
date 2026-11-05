#!/usr/bin/env bash
# Rebuild the child-pat edition: DQX Studio (DQX v0.16.0 + patches/) as ../app/ (code + prebuilt UI) and the wheel
# packs (dist/dqx-studio-wheels-<k>-of-<n>.zip + ../wheels.json). Run on a machine WITH internet/proxy access.
#
#   build/build_bundle.sh        # output: replaces ../app/src, ../app/requirements.txt, ../wheels.json; packs in ../dist
#
# Needs: git, node + yarn (1.x), uv, python3.11. Env overrides:
#   DQX_REF=v0.16.0  DQX_REPO=https://github.com/databrickslabs/dqx.git  DQX_SRC=<existing clone>
#   NPM_REGISTRY=<registry url>  (default: whatever yarn/npm is configured with)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DQX_REF="${DQX_REF:-v0.16.0}"
DQX_REPO="${DQX_REPO:-https://github.com/databrickslabs/dqx.git}"
WORK="${WORK:-$HERE/.build}"
OUT="$(cd "$HERE/.." && pwd)"
B="$WORK/stage"                      # staging: app/, app_wheels/, cluster_wheels/
PLATFORMS=(--platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64
           --platform manylinux_2_5_x86_64 --platform manylinux1_x86_64 --platform any)

step() { printf '\n==> %s\n' "$*"; }
rm -rf "$WORK" && mkdir -p "$WORK"

step "1/7 DQX source at $DQX_REF + patches/"
if [ -n "${DQX_SRC:-}" ]; then git clone --quiet "$DQX_SRC" "$WORK/dqx"; else git clone --quiet "$DQX_REPO" "$WORK/dqx"; fi
git -C "$WORK/dqx" -c advice.detachedHead=false checkout --quiet "$DQX_REF"
for p in "$HERE"/patches/*.patch; do
  git -C "$WORK/dqx" -c user.name=build -c user.email=build@local am --quiet "$p"
  echo "applied $(basename "$p")"
done
APP="$WORK/dqx/app"

step "2/7 Frontend (yarn + vite), AI and contract import compiled out"
( cd "$APP"
  yarn install --frozen-lockfile --prefer-offline --network-timeout 300000 ${NPM_REGISTRY:+--registry "$NPM_REGISTRY"} >/dev/null
  printf 'app_name = "DQX Studio"\napp_module = "databricks_labs_dqx_app.backend.app:app"\napp_slug = "databricks_labs_dqx_app"\napi_prefix = "/api"\n' \
    > src/databricks_labs_dqx_app/_metadata.py
  printf 'version = "0.15.0+airgap.1"\n' > src/databricks_labs_dqx_app/_version.py
  node_modules/.bin/tsc --noEmit -p tsconfig.json
  VITE_DQX_AI_DISABLED=1 VITE_DQX_CONTRACTS_DISABLED=1 node_modules/.bin/vite build >/dev/null )
test -f "$APP/src/databricks_labs_dqx_app/__dist__/index.html"

step "3/7 App source tree (no ui/ sources, no pyproject/uv.lock so the Apps build uses pip)"
mkdir -p "$B/app/src"
rsync -a --exclude 'ui/' --exclude '__pycache__/' "$APP/src/databricks_labs_dqx_app" "$B/app/src/"
# Databricks License: prominent notice on every shipped source file the patches modify.
for f in backend/sql_executor.py backend/services/ai_rules_service.py backend/pg_executor.py backend/sql_utils.py; do
  t="$B/app/src/databricks_labs_dqx_app/$f"
  { echo "# MODIFIED: changed from databrickslabs/dqx v0.16.0 for an air-gapped, permission-limited install. See PATCHES.md."; cat "$t"; } > "$t.new" && mv "$t.new" "$t"
done

step "4/7 App wheelhouse: linux x86_64, CPython 3.11 (the Apps runtime's Python)"
WH="$B/app_wheels"; mkdir -p "$WH" "$WORK/sdist"
grep -vE '^(thrift|pandas|pyarrow|numpy)==' "$HERE/deps/app-requirements.lock.txt" > "$WORK/app-req.nothrift.txt"
python3.11 -m pip download -q --no-deps --only-binary=:all: --implementation cp --python-version 3.11 \
  "${PLATFORMS[@]}" -r "$WORK/app-req.nothrift.txt" -d "$WH"
# thrift 0.20.0 (databricks-sql-connector) is published as an sdist only: build a pure-Python wheel.
python3.11 -m pip download -q --no-deps --no-binary=:all: thrift==0.20.0 -d "$WORK/sdist"
tar -xzf "$WORK/sdist/thrift-0.20.0.tar.gz" -C "$WORK/sdist"
sed -i.bak 's/^    with_binary = True$/    with_binary = False/' "$WORK/sdist/thrift-0.20.0/setup.py"
python3.11 -m pip wheel -q --no-deps "$WORK/sdist/thrift-0.20.0" -w "$WH"
# pandas / pyarrow / numpy come from the Databricks Apps runtime (pre-installed), so they are ranges, not vendored.
{ printf -- '--no-index\n--find-links ./wheels\n'; sed -E \
    -e 's/^pandas==.*/pandas>=2.2,<2.4  # from the Databricks Apps runtime (pre-installed); not vendored/' \
    -e 's/^pyarrow==.*/pyarrow>=16,<25  # from the Databricks Apps runtime (pre-installed); not vendored/' \
    -e 's/^numpy==.*/numpy>=1.26,<2  # from the Databricks Apps runtime (pre-installed); not vendored/' \
    "$HERE/deps/app-requirements.lock.txt"; } > "$B/app/requirements.txt"
# Prove the wheelhouse is complete: resolve every pin OFFLINE for the Apps runtime (linux x86_64, CPython 3.11).
# pandas / pyarrow / numpy stand in for the runtime's pre-installed copies: downloaded for this check only, never shipped.
STANDIN="$WORK/preinstalled_standin"; mkdir -p "$STANDIN"
python3.11 -m pip download -q --no-deps --only-binary=:all: --implementation cp --python-version 3.11 "${PLATFORMS[@]}" \
  pandas==2.2.3 pyarrow==16.1.0 numpy==1.26.4 -d "$STANDIN"
python3.11 -m pip install -q --dry-run --ignore-installed --no-index --find-links "$WH" --find-links "$STANDIN" --only-binary=:all: \
  --implementation cp --python-version 3.11 "${PLATFORMS[@]}" --target "$WORK/dry_app" -r "$B/app/requirements.txt"
echo "app wheelhouse resolves offline: $(ls "$WH" | wc -l | tr -d ' ') wheels"

step "5/7 Cluster wheelhouse: CPython 3.11 (DBR 15.4 LTS) and 3.12 (DBR 16.4 LTS), x86_64, + the task-runner wheel"
CW="$B/cluster_wheels"; mkdir -p "$CW"
for PY in 3.11 3.12; do
  python3.11 -m pip download -q --no-deps --only-binary=:all: --implementation cp --python-version "$PY" \
    "${PLATFORMS[@]}" -r "$HERE/deps/cluster-requirements.lock.txt" -d "$CW"
done
( cd "$APP" && uv build tasks/ --wheel --out-dir "$CW" >/dev/null ) && rm -f "$CW/.gitignore"
RUNNER_PKGS=(databricks-labs-dqx==0.16.0 databricks-sdk==0.120.0 databricks-labs-dqx-task-runner==0.1.0)
for PY in 3.11 3.12; do
  python3.11 -m pip install -q --dry-run --ignore-installed --no-index --find-links "$CW" --only-binary=:all: \
    --implementation cp --python-version "$PY" "${PLATFORMS[@]}" --target "$WORK/dry_x86_$PY" "${RUNNER_PKGS[@]}"
done
echo "cluster wheelhouse resolves offline for Python 3.11 and 3.12 (x86_64): $(ls "$CW" | wc -l | tr -d ' ') wheels"

step "6/7 Replace ../app (code, prebuilt UI, requirements.txt) and the runner notebook"
# MODIFIED / ADDED headers (Databricks License: prominent notice on modified files)
for f in sql_executor.py services/ai_rules_service.py pg_executor.py sql_utils.py app.py dependencies.py \
         routes/v1/__init__.py routes/v1/roles.py routes/v1/schedules.py; do
  t="$B/app/src/databricks_labs_dqx_app/backend/$f"
  head -1 "$t" | grep -q '^# MODIFIED' || { echo "# MODIFIED: changed from databrickslabs/dqx v0.16.0 for an air-gapped, permission-limited install. See PATCHES.md."; cat "$t"; } > "$t.new" && mv "$t.new" "$t"
done
for f in browser_pat.py access_check.py; do
  t="$B/app/src/databricks_labs_dqx_app/backend/$f"
  { echo "# ADDED by the dqx-studio-airgap build (not part of upstream DQX). See PATCHES.md."; cat "$t"; } > "$t.new" && mv "$t.new" "$t"
done
rm -rf "$OUT/app/src" && mkdir -p "$OUT/app" && cp -R "$B/app/src" "$OUT/app/src" && cp "$B/app/requirements.txt" "$OUT/app/requirements.txt"
# Notebook sources travel as .py.txt: a .py starting "# Databricks notebook source" turns into a notebook on copy.
mkdir -p "$OUT/runner_job" && cp "$HERE/runner_job/dqx_task_runner_job.py" "$OUT/runner_job/dqx_task_runner_job.py.txt"

step "7/7 Wheel packs (each under 10 MiB) + ../wheels.json"
rm -rf "$OUT/dist" && python3 "$HERE/make_packs.py" "$WH" "$CW" --out "$OUT/dist"
ls -l "$OUT/dist"
