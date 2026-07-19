"""Local web dashboard for managing mini programs.

Runs a small HTTP server (stdlib only) inside the menu bar app process, so it
shares the same Config and ProcessManager instances. The menu opens this in the
default browser. It replaces the fiddly chained-dialog "Add" flow with a real
form, plus full launch/stop/status/log/remove management on one page.
"""

import html
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from config import Config, Program, KIND_PYTHON, KIND_SHELL
from process_manager import ProcessManager

LOG_TAIL_BYTES = 40_000


def _read_log_tail(prog: Program) -> str:
    if not os.path.exists(prog.log_path()):
        return "(no output yet)"
    with open(prog.log_path(), "rb") as f:
        try:
            f.seek(-LOG_TAIL_BYTES, os.SEEK_END)
        except OSError:
            f.seek(0)
        return f.read().decode("utf-8", "replace")


def _log_page(prog: Program) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(prog.name)} — Logs</title>
<style>
  :root {{ color-scheme: light dark; }}
  html, body {{ height: 100%; margin: 0; }}
  body {{ display:flex; flex-direction:column; font-family: -apple-system, system-ui, sans-serif;
         background: Canvas; color: CanvasText; }}
  header {{ padding: 12px 18px; border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
           display:flex; align-items:center; gap:10px; }}
  header h1 {{ font-size: 15px; margin: 0; font-weight: 620; }}
  .live {{ font-size: 11px; color:#1f8f4d; display:flex; align-items:center; gap:6px; }}
  .dot {{ width:8px; height:8px; border-radius:50%; background:#1f8f4d; animation: pulse 1.4s infinite; }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
  pre {{ flex:1; overflow:auto; margin:0; padding: 14px 18px; font-family: ui-monospace, monospace;
        font-size: 12.5px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
</style></head>
<body>
<header><h1>{html.escape(prog.name)}</h1>
  <span class="live"><span class="dot"></span>live</span></header>
<pre id="log">loading…</pre>
<script>
  const id = {json.dumps(prog.id)};
  const pre = document.getElementById('log');
  async function tick() {{
    try {{
      const r = await fetch('/lograw?id=' + encodeURIComponent(id));
      const t = await r.text();
      const atBottom = Math.abs(pre.scrollHeight - pre.scrollTop - pre.clientHeight) < 50;
      pre.textContent = t;
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    }} catch (e) {{}}
  }}
  tick();
  setInterval(tick, 1500);
</script>
</body></html>"""


def _page(config: Config, procs: ProcessManager) -> str:
    rows = []
    for p in config.programs:
        running = procs.is_running(p.id)
        kind_label = "Python" if p.kind == KIND_PYTHON else "Shell"
        badge = ('<span class="badge run">running</span>' if running
                 else '<span class="badge stop">stopped</span>')
        action = (f'<button name="action" value="stop" class="btn stop">Stop</button>'
                  f'<button name="action" value="restart" class="btn warn">Restart</button>'
                  if running else
                  f'<button name="action" value="launch" class="btn go">Launch</button>')
        rows.append(f"""
        <div class="card" data-id="{p.id}">
          <div class="card-head">
            <span class="name">{html.escape(p.name)}</span>
            {badge}
          </div>
          <div class="meta"><span class="kind">{kind_label}</span>
            <code>{html.escape(p.target)}</code></div>
          {f'<div class="meta args">args: <code>{html.escape(p.args)}</code></div>' if p.args else ''}
          {f'<div class="meta cwd">cwd: <code>{html.escape(p.cwd)}</code></div>' if p.cwd else ''}
          <div class="actions">
            <form method="post" action="/action">
              <input type="hidden" name="id" value="{p.id}">
              {action}
            </form>
            <a class="btn ghost" href="/log?id={p.id}" target="_blank">Logs</a>
            <form method="post" action="/action"
                  onsubmit="return confirm('Remove {html.escape(p.name)}?');">
              <input type="hidden" name="id" value="{p.id}">
              <button name="action" value="remove" class="btn danger">Remove</button>
            </form>
          </div>
        </div>""")

    empty = '' if config.programs else '<p class="empty">No mini programs yet — add one below.</p>'
    cards = "\n".join(rows)

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mini App Navigator</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
         background: Canvas; color: CanvasText; }}
  header {{ padding: 20px 24px; border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
           display:flex; align-items:center; gap:10px; }}
  header h1 {{ font-size: 20px; margin: 0; font-weight: 650; }}
  header .icon {{ font-size: 22px; }}
  main {{ max-width: 820px; margin: 0 auto; padding: 24px; }}
  .grid {{ display: grid; gap: 14px; }}
  .card {{ border: 1px solid color-mix(in srgb, CanvasText 14%, transparent);
          border-radius: 12px; padding: 16px; background: color-mix(in srgb, CanvasText 3%, Canvas); }}
  .card-head {{ display:flex; align-items:center; gap:10px; }}
  .name {{ font-weight: 620; font-size: 15px; }}
  .badge {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 600; }}
  .badge.run {{ background:#1f8f4d22; color:#1f8f4d; }}
  .badge.stop {{ background: color-mix(in srgb, CanvasText 12%, transparent); color: color-mix(in srgb, CanvasText 65%, transparent); }}
  .meta {{ font-size: 12.5px; margin-top: 6px; color: color-mix(in srgb, CanvasText 70%, transparent); }}
  .meta .kind {{ font-weight:600; margin-right:6px; }}
  code {{ font-family: ui-monospace, monospace; font-size: 12px;
         background: color-mix(in srgb, CanvasText 8%, transparent); padding: 1px 5px; border-radius: 5px; }}
  .actions {{ display:flex; gap:8px; margin-top: 12px; align-items:center; }}
  .actions form {{ margin:0; display:flex; gap:8px; }}
  .btn {{ font: inherit; font-size: 13px; font-weight: 550; padding: 6px 12px; border-radius: 8px;
         border: 1px solid transparent; cursor: pointer; text-decoration:none; display:inline-block; }}
  .btn.go {{ background:#2563eb; color:#fff; }}
  .btn.stop {{ background:#b4530f; color:#fff; }}
  .btn.warn {{ background: transparent; color:#b4530f;
              border-color: color-mix(in srgb, #b4530f 45%, transparent); }}
  .btn.danger {{ background: transparent; color:#c0392b; border-color: color-mix(in srgb, #c0392b 40%, transparent); }}
  .btn.ghost {{ background: transparent; color: CanvasText; border-color: color-mix(in srgb, CanvasText 22%, transparent); }}
  .empty {{ color: color-mix(in srgb, CanvasText 55%, transparent); }}
  h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: .04em;
       color: color-mix(in srgb, CanvasText 55%, transparent); margin: 32px 0 12px; }}
  form.add {{ border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); border-radius: 12px;
             padding: 18px; display: grid; gap: 12px; }}
  label {{ display:grid; gap:5px; font-size: 13px; font-weight: 550; }}
  input, select {{ font: inherit; padding: 8px 10px; border-radius: 8px;
    border: 1px solid color-mix(in srgb, CanvasText 22%, transparent); background: Canvas; color: CanvasText; }}
  .row {{ display:grid; grid-template-columns: 160px 1fr; gap: 12px; }}
  .submit {{ background:#2563eb; color:#fff; border:none; padding: 9px 16px; justify-self:start; }}
  .hint {{ font-weight: 400; color: color-mix(in srgb, CanvasText 55%, transparent); font-size: 12px; }}
</style></head>
<body>
<header><span class="icon">🚀</span><h1>Mini App Navigator</h1></header>
<main>
  {empty}
  <div class="grid">{cards}</div>

  <h2>Add mini program</h2>
  <form class="add" method="post" action="/add">
    <label>Name
      <input name="name" required placeholder="e.g. Data Cleaner">
    </label>
    <div class="row">
      <label>Kind
        <select name="kind">
          <option value="{KIND_SHELL}">Shell command / app</option>
          <option value="{KIND_PYTHON}">Python script</option>
        </select>
      </label>
      <label>Target
        <input name="target" required
               placeholder="open -a Calculator   OR   /path/to/script.py">
        <span class="hint">Shell command to run, or absolute path to a .py file.</span>
      </label>
    </div>
    <div class="row">
      <label>Arguments <span class="hint">(optional)</span>
        <input name="args" placeholder="--flag value">
      </label>
      <label>Working dir <span class="hint">(optional)</span>
        <input name="cwd" placeholder="/path/to/dir">
      </label>
    </div>
    <button class="btn submit" type="submit">Add program</button>
  </form>
</main>
<script>
  // Live-refresh running badges without a full reload.
  async function poll() {{
    try {{
      const r = await fetch('/api/status');
      const s = await r.json();
      document.querySelectorAll('.card').forEach(c => {{
        const running = s[c.dataset.id];
        const b = c.querySelector('.badge');
        if (!b) return;
        const wasRunning = b.classList.contains('run');
        if (running !== wasRunning) location.reload();  // action changed -> refresh buttons
      }});
    }} catch (e) {{}}
  }}
  setInterval(poll, 3000);
</script>
</body></html>"""


class DashboardServer:
    """Owns the HTTP server thread and the shared config/process state."""

    def __init__(self, config: Config, procs: ProcessManager, port: int = 8765):
        self.config = config
        self.procs = procs
        self.port = port
        self._httpd = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def ensure_started(self):
        """Start the HTTP server on first use.

        Called lazily (when the dashboard or a log page is opened) instead of at
        app startup, so the server thread + socket cost nothing until needed.
        Idempotent: safe to call on every open.
        """
        if self._httpd is not None:
            return
        server = self  # capture for the handler

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence console noise
                pass

            def _redirect_home(self):
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()

            def _read_form(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                return {k: v[0] for k, v in parse_qs(body).items()}

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    body = _page(server.config, server.procs).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/status":
                    status = {p.id: server.procs.is_running(p.id) for p in server.config.programs}
                    body = json.dumps(status).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/log":
                    qs = parse_qs(parsed.query)
                    prog = server.config.get(qs.get("id", [""])[0])
                    if prog is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    body = _log_page(prog).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/lograw":
                    qs = parse_qs(parsed.query)
                    prog = server.config.get(qs.get("id", [""])[0])
                    text = _read_log_tail(prog) if prog else "Program not found."
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(text.encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/add":
                    form = self._read_form()
                    name = form.get("name", "").strip()
                    target = form.get("target", "").strip()
                    if name and target:
                        kind = KIND_PYTHON if form.get("kind") == KIND_PYTHON else KIND_SHELL
                        server.config.add(Program(
                            name=name, kind=kind, target=target,
                            args=form.get("args", "").strip(),
                            cwd=form.get("cwd", "").strip(),
                        ))
                    self._redirect_home()
                elif self.path == "/action":
                    form = self._read_form()
                    prog = server.config.get(form.get("id", ""))
                    action = form.get("action")
                    if prog:
                        if action == "launch":
                            server.procs.launch(prog)
                        elif action == "stop":
                            server.procs.stop(prog.id)
                        elif action == "restart":
                            server.procs.restart(prog)
                        elif action == "remove":
                            server.procs.stop(prog.id)
                            server.config.remove(prog.id)
                    self._redirect_home()
                else:
                    self.send_response(404)
                    self.end_headers()

        # Try a small range so a busy port (e.g. another instance) doesn't
        # crash the whole app; self.port is updated to the one we bind.
        last_err = None
        for candidate in range(self.port, self.port + 10):
            try:
                self._httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
                self.port = candidate
                break
            except OSError as e:
                last_err = e
        if self._httpd is None:
            raise last_err
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None
