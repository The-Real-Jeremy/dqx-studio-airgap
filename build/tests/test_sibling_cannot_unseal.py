"""Must-fix 1 proof: two SEPARATE processes with identical App-ception child env (same client secret + app name).
Process A (Studio) seals a PAT; process B (a sibling child) is handed the sealed cookie and tries to open it."""
import json, os, subprocess, sys
SRC = sys.argv[1]
ENV = {**os.environ, "DQX_BROWSER_PAT": "1", "DATABRICKS_CLIENT_SECRET": "shared-host-secret", "DATABRICKS_APP_NAME": "app-ception",
       "DATABRICKS_HOST": "https://example.invalid", "PYTHONPATH": SRC}
code_seal = "from databricks_labs_dqx_app.backend import browser_pat as b; print(b.key_scope()); print(b.seal('dapiFAKE-not-a-token', 'alice@example.com'))"
code_open = "import sys; from databricks_labs_dqx_app.backend import browser_pat as b; print(b.key_scope()); print(b.unseal(sys.argv[1]))"
def run(code, env, *args):
    return subprocess.run([sys.executable, "-c", code, *args], env=env, capture_output=True, text=True).stdout.split("\n")
results = {}
for label, extra in (("child (APPCEPTION_CHILD=1)", {"APPCEPTION_CHILD": "1"}),
                     ("child, even if DEPLOY env said app scope", {"APPCEPTION_CHILD": "1", "DQX_PAT_KEY_SCOPE": "app"}),
                     ("standalone (DQX_PAT_KEY_SCOPE=app)", {"DQX_PAT_KEY_SCOPE": "app"})):
    env = {**ENV, **extra}
    scope_a, sealed = run(code_seal, env)[:2]
    scope_b, opened = run(code_open, env, sealed)[:2]
    results[label] = (scope_a, opened)
    print(f"{label:45s} key scope {scope_a:8s} | second process opens it: {opened}")
assert results["child (APPCEPTION_CHILD=1)"][1] == "None"
assert results["child, even if DEPLOY env said app scope"][1] == "None"
assert "dapiFAKE" in results["standalone (DQX_PAT_KEY_SCOPE=app)"][1]  # expected: own-secret key survives restarts
print("PASS: in child mode a sibling process with the same env cannot unseal the cookie")
