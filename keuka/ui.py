# ui.py
# -----------------------------------------------------------------------------
# UI helpers for a consistent, modern look:
#  - _base_css(): shared CSS (light/dark via prefers-color-scheme)
#  - render_page(): simple HTML shell with topbar + container
# -----------------------------------------------------------------------------

def _base_css():
    return """
    :root {
        --bg: #ffffff; --fg: #111; --muted:#666; --card:#f8f9fb; --border:#e5e7eb;
        --ok:#0a7d27; --warn:#b85c00; --crit:#a40000; --idle:#666;
        --link:#2563eb; --badge:#eef2ff;
    }
    @media (prefers-color-scheme: dark) {
        :root {
            --bg:#0b0f14; --fg:#e5e7eb; --muted:#94a3b8; --card:#0f1720; --border:#1f2937;
            --link:#60a5fa; --badge:#111827;
        }
        img { filter: brightness(0.95) contrast(1.05); }
    }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--fg); font: 15px/1.45 system-ui, Segoe UI, Roboto, Arial, sans-serif; }
    a { color: var(--link); text-decoration: none; }
    .topbar { position: sticky; top:0; z-index: 10; backdrop-filter: blur(6px);
              background: color-mix(in oklab, var(--bg) 85%, transparent);
              border-bottom: 1px solid var(--border); }
    .topbar-inner { display:flex; gap:1rem; align-items:center; justify-content:space-between; padding:.8rem 1rem; max-width:1100px; margin:0 auto; }
    .brand { font-weight: 700; letter-spacing:.2px; }
    nav a { margin-right:.8rem; }
    .container { max-width:1100px; margin: 1rem auto 2rem auto; padding: 0 1rem; }
    h1 { font-size:1.6rem; margin: .4rem 0 .8rem 0; }
    .muted { color: var(--muted); }
    .grid { display:grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .card { background: var(--card); border:1px solid var(--border); border-radius:14px; padding:1rem; }
    table { width:100%; border-collapse: collapse; }
    th, td { padding: .5rem .6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    th { text-align:left; width: 46%; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .btn { display:inline-block; padding:.5rem .8rem; border:1px solid var(--border); border-radius:10px; background:var(--bg); cursor:pointer; }
    .badge { display:inline-block; padding:.15rem .5rem; border-radius:999px; font-size:.8rem; border:1px solid var(--border); background:var(--badge); }
    .b-ok   { color:#fff; background: var(--ok); border-color: var(--ok); }
    .b-warn { color:#fff; background: var(--warn); border-color: var(--warn); }
    .b-crit { color:#fff; background: var(--crit); border-color: var(--crit); }
    .b-idle { color:#fff; background: var(--idle); border-color: var(--idle); }
    .flex { display:flex; align-items:center; gap:.6rem; }
    .right { margin-left:auto; }
    .bars { display:inline-flex; gap:2px; align-items:flex-end; height:12px; }
    .bars span { width:3px; background:#cbd5e1; border-radius:2px; opacity:.7; }
    .bars .on { background:#16a34a; opacity:1; }
    @media (prefers-color-scheme: dark) {
        .bars span { background:#334155; }
        .bars .on { background:#22c55e; }
    }
    /* change highlights */
    @keyframes flashUp { 0%{ background: rgba(22,163,74,.3);} 100%{ background: transparent;} }
    @keyframes flashDown { 0%{ background: rgba(220,38,38,.32);} 100%{ background: transparent;} }
    .upflash { animation: flashUp 1.2s ease-out; }
    .downflash { animation: flashDown 1.2s ease-out; }
    /* status dot */
    .dot { inline-size:.6rem; block-size:.6rem; border-radius:999px; background:#9ca3af; display:inline-block; }
    .dot.ok { background:#16a34a; }
    .dot.err { background:#dc2626; }
    /* image thumb */
    .thumb { width:100%; max-width: 420px; border-radius:10px; border:1px solid var(--border); display:block; }
    """

def render_page(title: str, body_html: str, extra_head: str = "") -> str:
    """Simple HTML shell used by all pages."""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{_base_css()}</style>
  {extra_head}
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">Keuka Sensor V1.5 by Matt Reidy © 2025 - All rights reserved.</div>
      <nav class="muted">
        <a href="/health">Health</a>
        <a href="/webcam">Webcam</a>
        <a href="/admin">Admin</a>
      </nav>
    </div>
  </header>
  <main class="container">
    {body_html}
  </main>
</body>
</html>"""
