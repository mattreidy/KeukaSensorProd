# keuka/admin/update.py
# -----------------------------------------------------------------------------
# Update page + updater JSON APIs
#
# Endpoints:
#   - GET  /admin/update         -> HTML page for code-only update (keuka/ folder)
#   - GET  /admin/version        -> local/remote commit SHAs (+ source + error)
#   - POST /admin/start_update   -> start updater
#   - POST /admin/cancel_update  -> cancel updater
#   - GET  /admin/status         -> updater status + logs
#
# Notes:
#   - HTML/JS is the same as the original (verbatim), just moved here.
#   - Depends on: updater (updater singleton, APP_ROOT, REPO_URL, SERVICE_NAME),
#     and version helpers.
# -----------------------------------------------------------------------------

from __future__ import annotations

from flask import Blueprint, Response
import json
from datetime import datetime, timezone  # added for ISO timestamps in JSON

from updater import updater, APP_ROOT, REPO_URL, SERVICE_NAME
from version import get_local_commit_with_source, get_remote_commit, short_sha

def attach(bp: Blueprint) -> None:
    @bp.route("/admin/update")
    def admin_update():
        _UPDATE_HTML = """
          <style>
            .topnav a { margin-right:.8rem; text-decoration:none; }
            .badge { display:inline-block;padding:.15rem .45rem;border-radius:.4rem;background:#444;color:#fff; }
            .badge.ok { background:#184; color:#fff; }
            .badge.warn { background:#a60; color:#fff; }
          </style>

          <div class="flex" style="gap:.8rem;align-items:center;margin-bottom:.3rem">
            <h1 style="margin:0">Update Code (keuka/ only)</h1>
            <span class="muted">Repo: %%REPO_URL%%</span>
          </div>

          <div class="topnav" style="margin:.4rem 0 .8rem 0;">
            <a href="/admin/wifi">Wi-Fi</a>
            <a href="/admin/update"><strong>Update Code</strong></a>
            <a href="/admin/duckdns">DuckDNS</a>
          </div>

          <div class="card">
            <h3>Version status</h3>
            <div id="versionRow" style="display:flex;gap:1rem;align-items:center;">
              <div>Local: <code id="localSha">-</code> <span class="muted" id="localSrc"></span></div>
              <div>Remote: <code id="remoteSha">-</code></div>
              <span id="verBadge" class="badge">checking...</span>
              <button id="btnRefreshVer" class="btn btn-secondary" onclick="refreshVersion()">Refresh</button>
              <span id="verErr" class="muted" style="margin-left:1rem;"></span>
            </div>
          </div>

          <div class="card">
            <h3>Code-only update (keuka/ folder)</h3>
            <p>This pulls the latest code from <code>%%REPO_URL%%</code> (shallow clone),
            stages only the <code>keuka/</code> directory, backs up the current code on this Pi,
            applies the update, and restarts <code>%%SERVICE_NAME%%</code>.</p>

            <div style="display:flex;gap:.5rem;align-items:center;margin:.6rem 0;">
              <button id="btnStart" onclick="startUpdate()" class="btn">Start Update</button>
              <button id="btnCancel" onclick="cancelUpdate()" class="btn btn-secondary" disabled>Cancel</button>
              <span id="stateBadge" class="badge">state: <span id="stateText">-</span></span>
            </div>

            <div>
              <strong>Status log</strong>
              <pre id="logbox" style="height:300px;overflow:auto;background:#111;color:#ddd;border:1px solid #333;padding:.5rem;border-radius:.4rem;"></pre>
            </div>
          </div>

          <script>
          const logbox = document.getElementById('logbox');
          const stateText = document.getElementById('stateText');
          const btnStart = document.getElementById('btnStart');
          const btnCancel = document.getElementById('btnCancel');
          const localSha = document.getElementById('localSha');
          const localSrc = document.getElementById('localSrc');
          const remoteSha = document.getElementById('remoteSha');
          const verBadge = document.getElementById('verBadge');
          const verErr = document.getElementById('verErr');
          let pollTimer = null;

          // ---- Local time helpers for ISO-8601 [UTC] timestamps in logs ----
          function toLocalFromIso(iso) {
            const d = new Date(iso);
            if (isNaN(d)) return iso;
            // Default locale/timezone of the browser; keep seconds for precision
            return d.toLocaleString(undefined, {
              year: 'numeric', month: '2-digit', day: '2-digit',
              hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
          }
          function transformLogLine(line) {
            // Convert leading [YYYY-MM-DDTHH:MM:SSZ] to local time
            // Example: "[2025-08-17T14:22:05Z] message" -> "[8/17/2025, 10:22:05 AM] message" (depending on locale)
            return line.replace(/^\\[(\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z)\\]/,
              (_, iso) => '[' + toLocalFromIso(iso) + ']');
          }
          function renderLogs(lines) {
            if (!Array.isArray(lines)) return;
            const mapped = lines.map(transformLogLine);
            const atBottom = (logbox.scrollTop + logbox.clientHeight + 8) >= logbox.scrollHeight;
            logbox.textContent = mapped.join('\\n');
            if (atBottom) logbox.scrollTop = logbox.scrollHeight;
          }

          function setButtons(state) {
            if (state === 'running') {
              btnStart.disabled = true;
              btnCancel.disabled = false;
            } else {
              btnStart.disabled = false;
              btnCancel.disabled = true;
            }
          }

          function setBadge(local, remote, err) {
            if (err) {
              verBadge.textContent = 'error';
              verBadge.className = 'badge';
              verErr.textContent = err;
              return;
            }
            verErr.textContent = '';
            if (local && remote && local !== remote) {
              verBadge.textContent = 'Update available';
              verBadge.className = 'badge warn';
            } else if (local && remote) {
              verBadge.textContent = 'Up to date';
              verBadge.className = 'badge ok';
            } else {
              verBadge.textContent = 'Unknown';
              verBadge.className = 'badge';
            }
          }

          async function refreshVersion() {
            verBadge.textContent = 'checking...';
            verErr.textContent = '';
            try {
              const r = await fetch('/admin/version?cb=' + Date.now(), { headers: { 'Accept': 'application/json' }});
              const txt = await r.text();
              let v;
              try { v = JSON.parse(txt); } catch (e) { throw new Error(txt.slice(0,200)); }
              localSha.textContent = v.local_short || '-';
              localSrc.textContent = v.local_source ? '(' + v.local_source + ')' : '';
              remoteSha.textContent = v.remote_short || '-';
              setBadge(v.local, v.remote, v.error);
            } catch (e) {
              setBadge(null, null, e.message || 'fetch failed');
            }
          }

          async function startUpdate() {
            btnStart.disabled = true;
            try { await fetch('/admin/start_update', { method: 'POST' }); }
            catch (e) { appendLog('Failed to start: ' + e.message); }
            finally { setTimeout(pollStatus, 200); }
          }

          async function cancelUpdate() {
            try { await fetch('/admin/cancel_update', { method: 'POST' }); }
            catch (e) { appendLog('Failed to cancel: ' + e.message); }
          }

          function appendLog(line) {
            // For ad-hoc lines we won't inject timestamps; still show as-is.
            const atBottom = (logbox.scrollTop + logbox.clientHeight + 8) >= logbox.scrollHeight;
            const rendered = transformLogLine(line || '');
            logbox.textContent = rendered
              ? (logbox.textContent + (rendered.endsWith('\\n') ? rendered : (rendered + '\\n')))
              : logbox.textContent;
            if (atBottom) logbox.scrollTop = logbox.scrollHeight;
          }

          async function pollStatus() {
            try {
              const r = await fetch('/admin/status?cb=' + Date.now(), { headers: { 'Accept':'application/json' } });
              const s = await r.json();
              stateText.textContent = s.state;
              setButtons(s.state);
              if (Array.isArray(s.logs) && s.logs.length) renderLogs(s.logs);
              if (s.state === 'running') {
                pollTimer = setTimeout(pollStatus, 600);
              } else {
                await refreshVersion();
                let tries = 8;
                const tick = async () => {
                  await new Promise(res => setTimeout(res, 1500));
                  await refreshVersion();
                  if (--tries > 0) tick();
                };
                tick();
              }
            } catch (e) {
              appendLog('[note] status temporarily unavailable...');
              pollTimer = setTimeout(pollStatus, 1200);
            }
          }

          refreshVersion();
          pollStatus();
          </script>
        """
        body = (_UPDATE_HTML
                .replace("%%REPO_URL%%", REPO_URL)
                .replace("%%SERVICE_NAME%%", SERVICE_NAME))
        from ui import render_page
        return render_page("Keuka Sensor – Update Code", body)

    @bp.route("/admin/start_update", methods=["POST"])
    def admin_start_update():
        started = updater.start()
        return Response(json.dumps({"started": started}), mimetype="application/json")

    @bp.route("/admin/cancel_update", methods=["POST"])
    def admin_cancel_update():
        updater.cancel()
        return Response(json.dumps({"canceled": True}), mimetype="application/json")

    @bp.route("/admin/status")
    def admin_status():
        state, logs, t0, t1 = updater.get_logs()

        def _iso(ts):
            if ts is None:
                return None
            try:
                return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return None

        return Response(json.dumps({
            "state": state,
            "logs": logs[-1000:],
            "started_at": t0,
            "finished_at": t1,
            "started_at_iso": _iso(t0),     # new: UTC ISO-8601 for easy client-side TZ conversion
            "finished_at_iso": _iso(t1),    # new: UTC ISO-8601 for easy client-side TZ conversion
        }), mimetype="application/json")

    @bp.route("/admin/version")
    def admin_version():
        err = None
        local = None
        local_source = "none"
        remote = None
        try:
            local, local_source = get_local_commit_with_source(APP_ROOT)
        except Exception as e:
            err = f"local: {e}"
        try:
            remote = get_remote_commit(REPO_URL)
        except Exception as e:
            err = (err + "; " if err else "") + f"remote: {e}"
        return Response(json.dumps({
            "local": local,
            "remote": remote,
            "local_short": short_sha(local),
            "remote_short": short_sha(remote),
            "local_source": local_source,
            "up_to_date": (bool(local) and bool(remote) and local == remote),
            "error": err
        }), mimetype="application/json")
