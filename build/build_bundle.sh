#!/usr/bin/env bash
# Rebuild ../bundle/: DQX Studio (from the DQX v0.16.0 release, plus patches/) packaged so an
# air-gapped workspace can install it from a notebook. Run on a machine WITH internet/proxy access.
#
#   build/build_bundle.sh        # output: replaces ../bundle/ (and writes ../SHA256SUMS.txt)
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
B="$WORK/dqx_studio_bundle"          # bundle root (becomes the zip's top folder)
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
WH="$B/app/wheels"; mkdir -p "$WH" "$WORK/sdist"
grep -v '^thrift==' "$HERE/deps/app-requirements.lock.txt" > "$WORK/app-req.nothrift.txt"
python3.11 -m pip download -q --no-deps --only-binary=:all: --implementation cp --python-version 3.11 \
  "${PLATFORMS[@]}" -r "$WORK/app-req.nothrift.txt" -d "$WH"
# thrift 0.20.0 (databricks-sql-connector) is published as an sdist only: build a pure-Python wheel.
python3.11 -m pip download -q --no-deps --no-binary=:all: thrift==0.20.0 -d "$WORK/sdist"
tar -xzf "$WORK/sdist/thrift-0.20.0.tar.gz" -C "$WORK/sdist"
sed -i.bak 's/^    with_binary = True$/    with_binary = False/' "$WORK/sdist/thrift-0.20.0/setup.py"
python3.11 -m pip wheel -q --no-deps "$WORK/sdist/thrift-0.20.0" -w "$WH"
{ printf -- '--no-index\n--find-links ./wheels\n'; cat "$HERE/deps/app-requirements.lock.txt"; } > "$B/app/requirements.txt"
# Prove the wheelhouse is complete: resolve every pin OFFLINE for the Apps runtime (linux x86_64, CPython 3.11).
python3.11 -m pip install -q --dry-run --ignore-installed --no-index --find-links "$WH" --only-binary=:all: \
  --implementation cp --python-version 3.11 "${PLATFORMS[@]}" --target "$WORK/dry_app" -r "$HERE/deps/app-requirements.lock.txt"
echo "app wheelhouse resolves offline: $(ls "$WH" | wc -l | tr -d ' ') wheels"

step "5/7 Cluster wheelhouse: CPython 3.11 (DBR 15.4 LTS) and 3.12 (DBR 16.4 LTS), x86_64 AND aarch64, + the task-runner wheel"
CW="$B/cluster_wheels"; mkdir -p "$CW"
ARM=(--platform manylinux_2_28_aarch64 --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 --platform any)
for PY in 3.11 3.12; do
  python3.11 -m pip download -q --no-deps --only-binary=:all: --implementation cp --python-version "$PY" \
    "${PLATFORMS[@]}" -r "$HERE/deps/cluster-requirements.lock.txt" -d "$CW"
  python3.11 -m pip download -q --no-deps --only-binary=:all: --implementation cp --python-version "$PY" \
    "${ARM[@]}" -r "$HERE/deps/cluster-requirements.lock.txt" -d "$CW"
done
( cd "$APP" && uv build tasks/ --wheel --out-dir "$CW" >/dev/null ) && rm -f "$CW/.gitignore"
# Resolve exactly what the runner job installs, offline, for each Python and architecture.
RUNNER_PKGS=(databricks-labs-dqx==0.16.0 databricks-sdk==0.120.0 databricks-labs-dqx-task-runner==0.1.0)
for PY in 3.11 3.12; do
  python3.11 -m pip install -q --dry-run --ignore-installed --no-index --find-links "$CW" --only-binary=:all: \
    --implementation cp --python-version "$PY" "${PLATFORMS[@]}" --target "$WORK/dry_x86_$PY" "${RUNNER_PKGS[@]}"
  python3.11 -m pip install -q --dry-run --ignore-installed --no-index --find-links "$CW" --only-binary=:all: \
    --implementation cp --python-version "$PY" "${ARM[@]}" --target "$WORK/dry_arm_$PY" "${RUNNER_PKGS[@]}"
done
echo "cluster wheelhouse resolves offline for Python 3.11 and 3.12, x86_64 and aarch64: $(ls "$CW" | wc -l | tr -d ' ') wheels"

step "6/7 Runner notebook, manifest"
# Notebook sources travel as .py.txt: a .py file that starts with "# Databricks notebook source" turns into a
# notebook when the setup notebook copies the bundle into /Workspace, and the copy then fails on it.
mkdir -p "$B/runner_job" && cp "$HERE/runner_job/dqx_task_runner_job.py" "$B/runner_job/dqx_task_runner_job.py.txt"
FILES=$(( $(find "$B" -type f | wc -l) + 1 ))   # + MANIFEST.json itself
cat > "$B/MANIFEST.json" <<EOF
{"bundle_version": "0.15.0+airgap.1", "dqx_ref": "$DQX_REF",
 "dqx_base_commit": "$(git -C "$WORK/dqx" rev-parse "$DQX_REF^{commit}")", "patches": $(ls "$HERE"/patches/*.patch | wc -l | tr -d ' '), "built_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
 "file_count": $FILES, "app_wheels": $(ls "$WH" | wc -l | tr -d ' '), "cluster_wheels": $(ls "$CW" | wc -l | tr -d ' ')}
EOF

step "7/7 Replace ../bundle and write checksums"
rm -rf "$OUT/bundle" && cp -R "$B" "$OUT/bundle"
( cd "$OUT" && find bundle setup_notebook.py -type f -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS.txt )
echo "bundle: $(find "$OUT/bundle" -type f | wc -l | tr -d ' ') files"
