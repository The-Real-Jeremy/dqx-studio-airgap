/*
 * Air-gap build: browser-held personal access token (PAT) and per-user access check.
 *
 * Shown only when the back end runs with DQX_BROWSER_PAT=1 (GET /api/v1/pat/status says mode "browser_pat").
 *  - First visit, or after the token expires: a screen asks for the user's own PAT. The back end checks it
 *    belongs to the signed-in user and returns it as an HttpOnly cookie, so this script never sees it again.
 *  - A small bar offers "Forget token" and "Check my access".
 *  - Once per browser session, and whenever an API call fails on a permission, it runs GET /api/v1/access-check
 *    and lists anything missing with the exact fix. No stack traces are ever shown.
 */
(function () {
  "use strict";
  var API = "/api/v1";
  var state = { mode: null, shownCheck: false, checking: false };

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "style") e.style.cssText = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }

  var FONT = "font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;";
  var OVERLAY = "position:fixed;inset:0;z-index:2147483000;background:rgba(15,23,42,.55);display:flex;align-items:center;justify-content:center;" + FONT;
  var CARD = "background:#fff;color:#0f172a;border-radius:12px;box-shadow:0 20px 50px rgba(0,0,0,.3);width:min(560px,92vw);max-height:88vh;overflow:auto;padding:28px 30px;";
  var BTN = "background:#1b3139;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:14px;font-weight:600;cursor:pointer;";
  var BTN2 = "background:transparent;color:#1b3139;border:1px solid #cbd5e1;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;";

  function call(method, path, body) {
    return window.__dqxOrigFetch(API + path, {
      method: method, credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.text().then(function (t) {
        var j = null; try { j = JSON.parse(t); } catch (e) { /* not JSON */ }
        return { status: r.status, json: j };
      });
    });
  }

  function detailText(j, fallback) {
    if (!j) return fallback;
    var d = j.detail;
    if (typeof d === "string") return d;
    if (d && d.message) return d.message;
    return fallback;
  }

  /* ── the token screen ─────────────────────────────────────────────────────── */
  function showTokenScreen(reason) {
    if (document.getElementById("dqx-pat-gate")) return;
    var input = el("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: "dapi…",
      style: "width:100%;box-sizing:border-box;padding:11px 12px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px;margin:6px 0 4px;" });
    var msg = el("div", { role: "alert", style: "min-height:20px;color:#b91c1c;font-size:13px;margin:6px 0 10px;white-space:pre-wrap;" });
    var save = el("button", { type: "submit", style: BTN, text: "Save and continue" });
    var form = el("form", {
      onsubmit: function (ev) {
        ev.preventDefault();
        msg.style.color = "#475569"; msg.textContent = "Checking the token with Databricks…"; save.disabled = true;
        call("POST", "/pat", { pat: input.value }).then(function (r) {
          input.value = "";
          if (r.status === 200) { msg.style.color = "#15803d"; msg.textContent = "Saved. Loading…"; location.reload(); return; }
          save.disabled = false; msg.style.color = "#b91c1c";
          msg.textContent = detailText(r.json, "The token was not accepted (HTTP " + r.status + ").");
        }).catch(function () { save.disabled = false; msg.style.color = "#b91c1c"; msg.textContent = "Could not reach the app. Check your connection and try again."; });
      },
    }, [
      el("h2", { text: "Enter your Databricks personal access token", style: "margin:0 0 10px;font-size:20px;" }),
      reason ? el("p", { text: reason, style: "margin:0 0 12px;padding:10px 12px;background:#fef2f2;border-radius:8px;color:#991b1b;font-size:14px;" }) : null,
      el("p", { style: "margin:0 0 12px;font-size:14px;line-height:1.5;color:#334155;", text:
        "DQX Studio starts and watches its data-quality jobs with your own token, so every run is done as you. " +
        "Create one in Databricks: your user icon > Settings > Developer > Access tokens > Generate new token." }),
      el("label", { text: "Personal access token", style: "font-size:13px;font-weight:600;" }), input, msg,
      el("ul", { style: "margin:0 0 18px;padding-left:18px;font-size:12.5px;line-height:1.55;color:#475569;list-style:disc outside;" }, [
        el("li", { text: "It is kept only in this browser, as a secure cookie that page scripts cannot read. It is not stored in the app." }),
        el("li", { text: "It must be your own: the app checks it belongs to the signed-in user." }),
        el("li", { text: "You are asked again only if you clear cookies, press Forget token, or the token expires (at most 30 days)." }),
        el("li", { text: "Scheduler is disabled in dynamic PAT mode: run rules on demand from Run Rules." }),
      ]),
      el("div", { style: "display:flex;gap:10px;justify-content:flex-end;" }, [save]),
    ]);
    document.body.appendChild(el("div", { id: "dqx-pat-gate", style: OVERLAY }, [el("div", { style: CARD }, [form])]));
    setTimeout(function () { input.focus(); }, 50);
  }

  /* ── the access panel ─────────────────────────────────────────────────────── */
  function showAccess(res, auto) {
    var old = document.getElementById("dqx-access"); if (old) old.remove();
    var missing = res.checks.filter(function (c) { return c.status !== "ok"; });
    if (auto && missing.length === 0) return;
    var rows = res.checks.map(function (c) {
      var colour = c.status === "ok" ? "#15803d" : c.status === "missing" ? "#b91c1c" : "#b45309";
      var mark = c.status === "ok" ? "OK" : c.status === "missing" ? "Missing" : "Could not check";
      return el("div", { style: "border-top:1px solid #e2e8f0;padding:10px 0;" }, [
        el("div", { style: "display:flex;justify-content:space-between;gap:10px;font-size:14px;font-weight:600;" }, [
          el("span", { text: c.check }), el("span", { text: mark, style: "color:" + colour + ";white-space:nowrap;" })]),
        c.status !== "ok" && c.detail ? el("div", { text: c.detail, style: "font-size:12.5px;color:#475569;margin-top:4px;white-space:pre-wrap;" }) : null,
        c.fix ? el("pre", { text: c.fix, style: "font-size:12px;background:#f1f5f9;border-radius:6px;padding:8px 10px;margin:6px 0 0;white-space:pre-wrap;" }) : null,
      ]);
    });
    var close = el("button", { style: BTN, text: "Close", onclick: function () { panel.remove(); } });
    var recheck = el("button", { style: BTN2, text: "Check again", onclick: function () { panel.remove(); runCheck(false); } });
    var panel = el("div", { id: "dqx-access", style: OVERLAY }, [el("div", { style: CARD }, [
      el("h2", { text: missing.length ? "You are missing some access" : "Your access is complete", style: "margin:0 0 6px;font-size:20px;" }),
      el("p", { text: "Signed in as " + res.user + ". Each item below names the exact fix.", style: "margin:0 0 8px;font-size:13px;color:#475569;" }),
    ].concat(rows).concat([el("div", { style: "display:flex;gap:10px;justify-content:flex-end;margin-top:14px;" }, [recheck, close])]))]);
    document.body.appendChild(panel);
  }

  function runCheck(auto) {
    if (state.checking) return; state.checking = true;
    call("GET", "/access-check").then(function (r) {
      state.checking = false;
      if (r.status === 200 && r.json) showAccess(r.json, auto);
    }).catch(function () { state.checking = false; });
  }

  /* ── the small bar ────────────────────────────────────────────────────────── */
  function showBar() {
    if (document.getElementById("dqx-pat-bar")) return;
    var bar = el("div", { id: "dqx-pat-bar", style: "position:fixed;left:12px;bottom:12px;z-index:2147482000;display:flex;gap:8px;align-items:center;background:#fff;border:1px solid #e2e8f0;border-radius:999px;padding:6px 8px 6px 14px;box-shadow:0 4px 14px rgba(0,0,0,.08);font-size:12.5px;color:#334155;" + FONT }, [
      el("span", { text: "Token saved in this browser" }),
      el("button", { style: "border:0;background:#f1f5f9;border-radius:999px;padding:5px 10px;cursor:pointer;font-size:12.5px;", text: "Check my access", onclick: function () { runCheck(false); } }),
      el("button", { style: "border:0;background:#fee2e2;color:#991b1b;border-radius:999px;padding:5px 10px;cursor:pointer;font-size:12.5px;", text: "Forget token",
        onclick: function () { call("DELETE", "/pat").then(function () { location.reload(); }); } }),
    ]);
    document.body.appendChild(bar);
  }

  /* ── watch every API response the app makes ──────────────────────────────── */
  var PERM = /PERMISSION_DENIED|does not have|not authori[sz]ed|insufficient privileges|Forbidden|CAN_USE|CAN_MANAGE_RUN/i;
  function inspect(status, text) {
    if (state.mode !== "browser_pat") return;
    if (status === 401 && /pat_required|pat_expired/.test(text || "")) {
      var j = null; try { j = JSON.parse(text); } catch (e) { /* ignore */ }
      showTokenScreen(detailText(j, "Your token needs to be entered again."));
    } else if ((status === 403 || status >= 500) && PERM.test(text || "")) {
      runCheck(false);
    }
  }
  window.__dqxOrigFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    return window.__dqxOrigFetch(input, init).then(function (r) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      if (url.indexOf("/api/") !== -1 && (r.status === 401 || r.status === 403 || r.status >= 500)) {
        r.clone().text().then(function (t) { inspect(r.status, t); }).catch(function () {});
      }
      return r;
    });
  };
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__dqxUrl = String(url || "");
    this.addEventListener("load", function () {
      if (this.__dqxUrl.indexOf("/api/") !== -1 && (this.status === 401 || this.status === 403 || this.status >= 500)) {
        var t = ""; try { t = typeof this.responseText === "string" ? this.responseText : ""; } catch (e) { /* binary */ }
        inspect(this.status, t);
      }
    });
    return origOpen.apply(this, arguments);
  };

  function start() {
    call("GET", "/pat/status").then(function (r) {
      if (r.status !== 200 || !r.json || r.json.mode !== "browser_pat") return;
      state.mode = "browser_pat";
      if (!r.json.valid) { showTokenScreen(r.json.has_pat ? "Your saved token no longer works (expired or revoked). Enter a new one." : ""); return; }
      showBar();
      if (!sessionStorage.getItem("dqx-access-checked")) { sessionStorage.setItem("dqx-access-checked", "1"); runCheck(true); }
    }).catch(function () { /* the app's own error handling takes over */ });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
