#!/usr/bin/env python3
"""Split the vendored wheels into "wheel pack" ZIPs that each stay under the 10 MiB Workspace Import limit.

  build/make_packs.py <app_wheels_dir> <cluster_wheels_dir> [--out dist] [--cap-mib 9.5]

Writes ../wheels.json (every wheel: pack, target, size, sha256; DEPLOY checks it) and
<out>/dqx-studio-wheels-<k>-of-<n>.zip, each holding one folder dqx-studio-wheels-<k>-of-<n>/{app,cluster}/.
Cluster wheels are x86_64 (and pure) only: Azure and most AWS classic node types. Rebuild for aarch64 if needed.
"""
import argparse, hashlib, json, os, sys, zipfile

ap = argparse.ArgumentParser()
ap.add_argument("app_dir"); ap.add_argument("cluster_dir")
ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "dist"))
ap.add_argument("--cap-mib", type=float, default=9.5)
a = ap.parse_args()
root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
files = []
for target, d in (("app", a.app_dir), ("cluster", a.cluster_dir)):
    for n in sorted(os.listdir(d)):
        if not n.endswith(".whl") or (target == "cluster" and "aarch64" in n):
            continue
        p = os.path.join(d, n)
        files.append({"name": n, "target": target, "size": os.path.getsize(p),
                      "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(), "_path": p})
cap = int(a.cap_mib * 1024 * 1024)
big = [f["name"] for f in files if f["size"] > cap]
if big:
    sys.exit(f"wheels larger than one pack: {big}")
bins: list[list[dict]] = []
for f in sorted(files, key=lambda f: -f["size"]):  # first-fit decreasing
    for b in bins:
        if sum(x["size"] for x in b) + f["size"] <= cap:
            b.append(f); break
    else:
        bins.append([f])
n = len(bins)
os.makedirs(a.out, exist_ok=True)
for k, b in enumerate(bins, 1):
    name = f"dqx-studio-wheels-{k}-of-{n}"
    zp = os.path.join(a.out, name + ".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
        for f in b:
            f["pack"] = k
            z.write(f["_path"], f"{name}/{f['target']}/{f['name']}")
    sz = os.path.getsize(zp)
    assert sz < 10 * 1024 * 1024, (zp, sz)
    print(f"{name}.zip  {len(b):3d} wheels  {sz / 1048576:5.2f} MiB")
manifest = {"packs": n, "pack_name": "dqx-studio-wheels-{k}-of-{n}",
            "files": [{k: v for k, v in f.items() if k != "_path"} for f in sorted(files, key=lambda f: (f["target"], f["name"]))]}
json.dump(manifest, open(os.path.join(root, "wheels.json"), "w"), indent=1)
print(f"wheels.json: {len(files)} wheels in {n} packs "
      f"({sum(f['target'] == 'app' for f in files)} app, {sum(f['target'] == 'cluster' for f in files)} cluster)")
