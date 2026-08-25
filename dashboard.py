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

from config import (Config, Program, Schedule, KIND_PYTHON, KIND_SHELL,
                    ACTION_LAUNCH, ACTION_STOP, ACTION_WINDOW,
                    SCHEDULE_ACTIONS, DAY_NAMES)
from process_manager import ProcessManager
from scheduler import Scheduler

LOG_TAIL_BYTES = 40_000

# Shown under the probe field in both the add and the edit form.
PROBE_HINT = (
    "Needed when the target only <em>launches</em> the program and exits "
    "(<code>open -a Foo.app</code>, or a script that backgrounds itself) — "
    "without it the navigator reports stopped while the program is up. "
    "A port number, or <code>match:</code> plus text from the real process's "
    "command line. Leave empty and a local dashboard URL above is used as the "
    "probe automatically."
)


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


def _apply_schedule_form(rule: Schedule, form: dict, raw: dict):
    """Copy one submitted rule form onto a Schedule.

    Whatever is submitted is kept as-is, even when it can't fire (no days, or a
    time the browser let through): the rule is then shown with a "never fires"
    marker, which beats silently discarding what was typed.
    """
    action = form.get("sched_action")
    rule.action = action if action in SCHEDULE_ACTIONS else ACTION_LAUNCH
    rule.at = form.get("at", "").strip() or rule.at
    rule.until = form.get("until", "").strip() or rule.until
    days = []
    for value in raw.get("days", []):
        try:
            day = int(value)
        except ValueError:
            continue
        if 0 <= day <= 6 and day not in days:
            days.append(day)
    rule.days = sorted(days)
    rule.enabled = bool(form.get("enabled"))  # unchecked boxes aren't submitted


def _schedule_from_form(form: dict, raw: dict) -> Schedule:
    rule = Schedule()
    _apply_schedule_form(rule, form, raw)
    return rule


def _day_boxes(selected) -> str:
    """The seven weekday checkboxes, Monday first."""
    chosen = set(selected)
    return "".join(
        f'<label class="day"><input type="checkbox" name="days" value="{i}"'
        f'{" checked" if i in chosen else ""}><span>{DAY_NAMES[i]}</span></label>'
        for i in range(7)
    )


def _rule_form(prog: Program, rule: Schedule = None) -> str:
    """One schedule rule as an editable row, or the blank "add" row.

    Add and edit are the same form so a rule reads the same whichever side of
    saving it is on; the only difference is the hidden rule id and the button.
    """
    adding = rule is None
    rule = rule or Schedule(action=ACTION_WINDOW, days=sorted(range(7)))
    launch_sel = ' selected' if rule.action == ACTION_LAUNCH else ''
    stop_sel = ' selected' if rule.action == ACTION_STOP else ''
    window_sel = ' selected' if rule.action == ACTION_WINDOW else ''
    # The "until" field only means anything for a window rule, so it is hidden
    # for the other two rather than sitting there asking to be filled in.
    until_hidden = '' if rule.is_window() else ' hidden'
    overnight = (' <span class="hint">next day</span>'
                 if rule.wraps_midnight() else '')
    on_checked = ' checked' if rule.enabled else ''
    sid = '' if adding else f'<input type="hidden" name="sid" value="{rule.id}">'
    warn = ('' if adding or rule.is_valid() else
            '<span class="bad" title="Pick at least one day, and a valid time">'
            'never fires</span>')
    if adding:
        buttons = ('<button name="action" value="add_schedule" class="btn submit">'
                   'Add rule</button>')
    else:
        buttons = ('<button name="action" value="save_schedule" class="btn submit">'
                   'Save</button>'
                   '<button name="action" value="remove_schedule" class="btn danger">'
                   'Remove</button>')
    return f"""
            <form method="post" action="/action" class="rule{' adding' if adding else ''}">
              <input type="hidden" name="id" value="{prog.id}">
              {sid}
              <div class="rule-top">
                <label class="toggle" title="Turn this rule off without deleting it">
                  <input type="checkbox" name="enabled"{on_checked}><span>on</span>
                </label>
                <select name="sched_action" aria-label="Action"
                        onchange="syncUntil(this)">
                  <option value="{ACTION_WINDOW}"{window_sel}>Launch &amp; stop</option>
                  <option value="{ACTION_LAUNCH}"{launch_sel}>Launch only</option>
                  <option value="{ACTION_STOP}"{stop_sel}>Stop only</option>
                </select>
                <input type="time" name="at" value="{html.escape(rule.at)}" required
                       aria-label="Time">
                <span class="until"{until_hidden}>
                  <span class="arrow">→</span>
                  <input type="time" name="until" value="{html.escape(rule.until)}"
                         aria-label="Stop time">{overnight}
                </span>
                {warn}
              </div>
              <div class="rule-days">
                {_day_boxes(rule.days)}
                <span class="presets">
                  <button type="button" onclick="setDays(this,'all')">Daily</button>
                  <button type="button" onclick="setDays(this,'week')">Weekdays</button>
                  <button type="button" onclick="setDays(this,'end')">Weekend</button>
                  <button type="button" onclick="setDays(this,'none')">Clear</button>
                </span>
              </div>
              <div class="rule-actions">{buttons}</div>
            </form>"""


def _schedule_block(prog: Program, sched: Scheduler) -> tuple:
    """(summary line for the card, hidden panel with the rule editors)."""
    rules = list(prog.schedules)
    if rules:
        labels = " · ".join(html.escape(r.label()) + ("" if r.enabled else " (off)")
                            for r in rules)
        upcoming = sched.summary(prog)
        next_bit = (f'<span class="hint"> — next: {html.escape(upcoming)}</span>'
                    if upcoming else '')
        summary = f'<div class="meta">schedule: {labels}{next_bit}</div>'
    else:
        summary = ''
    panel = f"""
          <div class="sched-panel" hidden>
            <div class="sched-title">Schedule
              <span class="hint">Times are local. A rule fires up to 15 minutes
                late if the Mac was asleep, and is skipped after that.</span>
            </div>
            {''.join(_rule_form(prog, r) for r in rules)}
            {_rule_form(prog)}
          </div>"""
    return summary, panel


def _card(p: Program, procs: ProcessManager, sched: Scheduler) -> str:
    """One program's card. Rendered on its own too, to refresh it in place
    after a schedule edit without reloading the whole page."""
    running = procs.is_running(p)
    kind_label = "Python" if p.kind == KIND_PYTHON else "Shell"
    badge = ('<span class="badge run">running</span>' if running
             else '<span class="badge stop">stopped</span>')
    if running and procs.is_adopted(p.id):
        # The navigator didn't spawn this one; its probe found it. Worth
        # saying, because Stop then signals a process we merely located.
        badge += ('<span class="badge probe" title="Found by this '
                  'program&#39;s liveness probe, not launched from here">'
                  'via probe</span>')
    action = (f'<button name="action" value="stop" class="btn stop">Stop</button>'
              f'<button name="action" value="restart" class="btn warn">Restart</button>'
              if running else
              f'<button name="action" value="launch" class="btn go">Launch</button>')
    url = p.dashboard_url()
    open_btn = (f'<a class="btn open" href="{html.escape(url)}" target="_blank" '
                f'rel="noopener">Open ↗</a>' if url else '')
    if url:
        url_line = f'<div class="meta">url: <code>{html.escape(url)}</code></div>'
    elif p.url.strip():
        # Kept as typed, but not openable — say so instead of silently
        # dropping the Open button.
        url_line = (f'<div class="meta">url: <code>{html.escape(p.url.strip())}</code>'
                    f'<span class="bad">invalid URL</span></div>')
    else:
        url_line = ''
    spec = p.probe_spec()
    if spec and not p.probe.strip():
        probe_line = (f'<div class="meta">probe: <code>{spec[1]}</code>'
                      f'<span class="hint"> (port, from the dashboard URL)</span></div>')
    elif spec:
        probe_line = (f'<div class="meta">probe: <code>{html.escape(str(spec[1]))}</code>'
                      f'<span class="hint"> ({spec[0]})</span></div>')
    elif p.probe.strip():
        probe_line = (f'<div class="meta">probe: <code>{html.escape(p.probe.strip())}</code>'
                      f'<span class="bad">invalid probe</span></div>')
    else:
        probe_line = ''
    sched_line, sched_panel = _schedule_block(p, sched)
    py_sel = ' selected' if p.kind == KIND_PYTHON else ''
    sh_sel = '' if p.kind == KIND_PYTHON else ' selected'
    auto_checked = ' checked' if p.auto_launch else ''
    editor = f"""
      <div class="editor" hidden>
        <form method="post" action="/action">
          <input type="hidden" name="id" value="{p.id}">
          <label>Name
            <input name="name" value="{html.escape(p.name)}" required>
          </label>
          <div class="row">
            <label>Kind
              <select name="kind">
                <option value="{KIND_SHELL}"{sh_sel}>Shell command / app</option>
                <option value="{KIND_PYTHON}"{py_sel}>Python script</option>
              </select>
            </label>
            <label>Target
              <input name="target" value="{html.escape(p.target)}" required
                     placeholder="open -a Calculator   OR   /path/to/script.py">
              <span class="hint">Shell command to run, or absolute path to a .py file.</span>
            </label>
          </div>
          <div class="row">
            <label>Arguments <span class="hint">(optional)</span>
              <input name="args" value="{html.escape(p.args)}" placeholder="--flag value">
            </label>
            <label>Working dir <span class="hint">(optional)</span>
              <input name="cwd" value="{html.escape(p.cwd)}" placeholder="/path/to/dir">
            </label>
          </div>
          <label>Dashboard URL <span class="hint">(optional)</span>
            <input name="url" value="{html.escape(p.url)}" placeholder="http://127.0.0.1:8501">
            <span class="hint">Leave empty to remove the Open button.</span>
          </label>
          <label>Liveness probe <span class="hint">(optional)</span>
            <input name="probe" value="{html.escape(p.probe)}"
                   placeholder="8787   OR   match:Stats.app/Contents/MacOS/Stats">
            <span class="hint">{PROBE_HINT}</span>
          </label>
          <div class="editor-actions">
            <button name="action" value="edit" class="btn submit">Save changes</button>
            <button type="button" class="btn ghost"
                    onclick="toggleEdit(this)">Cancel</button>
            <span class="hint">Changes apply to the next launch; restart a
              running program to pick them up.</span>
          </div>
        </form>
      </div>"""
    return f"""
    <div class="card" data-id="{p.id}">
      <div class="card-head">
        <span class="name">{html.escape(p.name)}</span>
        {badge}
        <form method="post" action="/action" class="autoform">
          <input type="hidden" name="id" value="{p.id}">
          <input type="hidden" name="action" value="set_auto">
          <label class="toggle" title="Launch this program when the navigator starts">
            <input type="checkbox" name="auto"
                   onchange="this.form.requestSubmit()"{auto_checked}>
            <span>Auto launch</span>
          </label>
        </form>
      </div>
      <div class="meta"><span class="kind">{kind_label}</span>
        <code>{html.escape(p.target)}</code></div>
      {f'<div class="meta args">args: <code>{html.escape(p.args)}</code></div>' if p.args else ''}
      {f'<div class="meta cwd">cwd: <code>{html.escape(p.cwd)}</code></div>' if p.cwd else ''}
      {url_line}
      {probe_line}
      {sched_line}
      <div class="actions">
        <form method="post" action="/action">
          <input type="hidden" name="id" value="{p.id}">
          {action}
        </form>
        {open_btn}
        <button type="button" class="btn ghost" onclick="toggleEdit(this)">Edit</button>
        <button type="button" class="btn ghost" onclick="toggleSched(this)">⏰ Schedule</button>
        <a class="btn ghost" href="/log?id={p.id}" target="_blank">Logs</a>
        <form method="post" action="/action"
              onsubmit="return confirm('Remove {html.escape(p.name)}?');">
          <input type="hidden" name="id" value="{p.id}">
          <button name="action" value="remove" class="btn danger">Remove</button>
        </form>
      </div>
      {editor}
      {sched_panel}
    </div>"""


def _page(config: Config, procs: ProcessManager, sched: Scheduler) -> str:
    rows = [_card(p, procs, sched) for p in config.programs]

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
  /* Brief confirmation that an in-page edit landed, in place of the reload
     that used to be the only sign anything happened. */
  .card.saved {{ border-color: color-mix(in srgb, #1f8f4d 55%, transparent);
                box-shadow: 0 0 0 3px #1f8f4d1f; }}
  .card {{ transition: border-color .25s ease, box-shadow .25s ease; }}
  .name {{ font-weight: 620; font-size: 15px; }}
  .badge {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 600; }}
  .badge.run {{ background:#1f8f4d22; color:#1f8f4d; }}
  .badge.stop {{ background: color-mix(in srgb, CanvasText 12%, transparent); color: color-mix(in srgb, CanvasText 65%, transparent); }}
  .badge.probe {{ background: transparent; color: color-mix(in srgb, CanvasText 55%, transparent);
                 border: 1px solid color-mix(in srgb, CanvasText 18%, transparent); cursor: help; }}
  .meta {{ font-size: 12.5px; margin-top: 6px; color: color-mix(in srgb, CanvasText 70%, transparent); }}
  .meta .kind {{ font-weight:600; margin-right:6px; }}
  code {{ font-family: ui-monospace, monospace; font-size: 12px;
         background: color-mix(in srgb, CanvasText 8%, transparent); padding: 1px 5px; border-radius: 5px; }}
  .autoform {{ margin: 0 0 0 auto; }}
  .toggle {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
            font-weight: 550; cursor: pointer;
            color: color-mix(in srgb, CanvasText 65%, transparent); }}
  .toggle input {{ margin: 0; accent-color: #2563eb; }}
  .toggle:hover {{ color: CanvasText; }}
  .editor {{ margin-top: 12px; padding-top: 12px;
            border-top: 1px solid color-mix(in srgb, CanvasText 12%, transparent); }}
  .editor form {{ display: grid; gap: 12px; }}
  .editor-actions {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  .sched-panel {{ margin-top: 12px; padding-top: 12px;
                 border-top: 1px solid color-mix(in srgb, CanvasText 12%, transparent); }}
  .sched-title {{ font-size: 13px; font-weight: 620; margin-bottom: 10px; }}
  .sched-title .hint {{ display: block; font-weight: 400; margin-top: 3px; }}
  .rule {{ display: grid; gap: 8px; padding: 10px; margin-bottom: 8px; border-radius: 10px;
          border: 1px solid color-mix(in srgb, CanvasText 12%, transparent);
          background: color-mix(in srgb, CanvasText 3%, Canvas); }}
  .rule.adding {{ border-style: dashed; background: transparent; }}
  .rule-top {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
  .rule-top select, .rule-top input[type="time"] {{ padding: 5px 8px; font-size: 13px; }}
  .until {{ display: inline-flex; align-items: center; gap: 6px; }}
  .until[hidden] {{ display: none; }}
  .arrow {{ color: color-mix(in srgb, CanvasText 45%, transparent); }}
  .rule-days {{ display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }}
  .day {{ display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
         font-weight: 500; padding: 3px 8px; border-radius: 999px; cursor: pointer;
         border: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }}
  .day input {{ margin: 0; accent-color: #2563eb; }}
  .day:has(input:checked) {{ border-color: color-mix(in srgb, #2563eb 55%, transparent);
                            background: #2563eb14; }}
  .presets {{ display: inline-flex; gap: 4px; margin-left: auto; }}
  .presets button {{ font: inherit; font-size: 11.5px; padding: 3px 8px; border-radius: 7px;
                    cursor: pointer; background: transparent; color: CanvasText;
                    border: 1px solid color-mix(in srgb, CanvasText 18%, transparent); }}
  .rule-actions {{ display: flex; gap: 8px; }}
  .rule-actions .btn {{ padding: 5px 11px; font-size: 12.5px; }}
  .bad {{ font-size: 11px; padding: 1px 7px; border-radius: 999px; font-weight: 600;
         background:#c0392b22; color:#c0392b; }}
  .actions {{ display:flex; gap:8px; margin-top: 12px; align-items:center; }}
  .actions form {{ margin:0; display:flex; gap:8px; }}
  .btn {{ font: inherit; font-size: 13px; font-weight: 550; padding: 6px 12px; border-radius: 8px;
         border: 1px solid transparent; cursor: pointer; text-decoration:none; display:inline-block; }}
  .btn.go {{ background:#2563eb; color:#fff; }}
  .btn.stop {{ background:#b4530f; color:#fff; }}
  .btn.warn {{ background: transparent; color:#b4530f;
              border-color: color-mix(in srgb, #b4530f 45%, transparent); }}
  .btn.danger {{ background: transparent; color:#c0392b; border-color: color-mix(in srgb, #c0392b 40%, transparent); }}
  .btn.open {{ background: transparent; color:#2563eb; border-color: color-mix(in srgb, #2563eb 45%, transparent); }}
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
    <label>Dashboard URL <span class="hint">(optional)</span>
      <input name="url" placeholder="http://127.0.0.1:8501">
      <span class="hint">If the program serves its own web UI, an Open button
        appears here and in the menu bar submenu.</span>
    </label>
    <label>Liveness probe <span class="hint">(optional)</span>
      <input name="probe" placeholder="8787   OR   match:Stats.app/Contents/MacOS/Stats">
      <span class="hint">{PROBE_HINT}</span>
    </label>
    <label class="toggle"><input type="checkbox" name="auto">
      <span>Auto launch when the navigator starts</span></label>
    <button class="btn submit" type="submit">Add program</button>
  </form>
</main>
<script>
  function toggleEdit(btn) {{
    const editor = btn.closest('.card').querySelector('.editor');
    editor.hidden = !editor.hidden;
    if (!editor.hidden) editor.querySelector('input')?.focus();
  }}

  function toggleSched(btn) {{
    const panel = btn.closest('.card').querySelector('.sched-panel');
    panel.hidden = !panel.hidden;
  }}

  // The stop time belongs to "Launch & stop" rules only.
  function syncUntil(select) {{
    const until = select.closest('.rule-top').querySelector('.until');
    until.hidden = select.value !== '{ACTION_WINDOW}';
  }}

  // Preset buttons just tick the day boxes; the days themselves are what's saved.
  const DAY_SETS = {{ all: [0,1,2,3,4,5,6], week: [0,1,2,3,4], end: [5,6], none: [] }};
  function setDays(btn, which) {{
    const want = DAY_SETS[which];
    btn.closest('form').querySelectorAll('input[name="days"]')
       .forEach(b => {{ b.checked = want.includes(Number(b.value)); }});
  }}

  // Schedule edits and the auto-launch toggle re-render just their own card,
  // so the panel stays open, the page keeps its scroll, and a run of edits
  // doesn't fight a full reload each time.
  document.addEventListener('submit', ev => {{
    const form = ev.target;
    if (form.matches('.rule, .autoform')) inlineSubmit(ev, form);
  }});

  async function inlineSubmit(ev, form) {{
    ev.preventDefault();
    const card = form.closest('.card');
    const body = new URLSearchParams(new FormData(form));
    // A button's own name/value isn't in FormData — that's what says which
    // action was pressed (Save vs Remove vs Add rule).
    if (ev.submitter && ev.submitter.name) {{
      body.append(ev.submitter.name, ev.submitter.value);
    }}
    const wasOpen = !card.querySelector('.sched-panel').hidden;
    try {{
      const r = await fetch('/action', {{
        method: 'POST', headers: {{'X-Fragment': '1'}}, body
      }});
      if (!r.ok) throw new Error(r.status);
      const holder = document.createElement('div');
      holder.innerHTML = (await r.text()).trim();
      const fresh = holder.firstElementChild;
      if (!fresh) {{ card.remove(); return; }}
      card.replaceWith(fresh);
      if (wasOpen) fresh.querySelector('.sched-panel').hidden = false;
      fresh.classList.add('saved');
      setTimeout(() => fresh.classList.remove('saved'), 700);
    }} catch (e) {{
      form.submit();   // fall back to the plain full-page post
    }}
  }}

  // A card is being edited if its editor or schedule panel is open. Such a card
  // is left alone by the poll — but only that card, so one open panel can't
  // freeze the running status of everything else on the page.
  function isEditing(card) {{
    return !!card.querySelector('.editor:not([hidden]), .sched-panel:not([hidden])');
  }}

  async function refreshCard(card) {{
    const r = await fetch('/card?id=' + encodeURIComponent(card.dataset.id));
    if (r.status === 404) {{ card.remove(); return; }}   // removed elsewhere
    if (!r.ok) return;
    const holder = document.createElement('div');
    holder.innerHTML = (await r.text()).trim();
    if (holder.firstElementChild) card.replaceWith(holder.firstElementChild);
  }}

  // Keep running status live. Each card is refreshed on its own, so the page
  // never reloads out from under a half-typed edit.
  async function poll() {{
    try {{
      const s = await (await fetch('/api/status')).json();
      const cards = [...document.querySelectorAll('.card')];
      // A program added or removed in another tab changes the set of ids;
      // that needs the whole page, so only do it when nothing is being edited.
      const known = cards.map(c => c.dataset.id).sort().join();
      if (known !== Object.keys(s).sort().join()) {{
        if (!cards.some(isEditing)) location.reload();
        return;
      }}
      for (const c of cards) {{
        const badge = c.querySelector('.badge');
        if (!badge || isEditing(c)) continue;
        if (s[c.dataset.id] !== badge.classList.contains('run')) await refreshCard(c);
      }}
    }} catch (e) {{}}
  }}
  setInterval(poll, 3000);
</script>
</body></html>"""


class DashboardServer:
    """Owns the HTTP server thread and the shared config/process state."""

    def __init__(self, config: Config, procs: ProcessManager, sched: Scheduler,
                 port: int = 8765):
        self.config = config
        self.procs = procs
        self.sched = sched
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

            def _done(self, program_id: str):
                """Answer a POST: one card back for an in-page edit, otherwise
                the usual redirect so the form still works without JavaScript."""
                if not self.headers.get("X-Fragment"):
                    self._redirect_home()
                    return
                prog = server.config.get(program_id)
                # An empty body means the program is gone; the page drops the card.
                body = (_card(prog, server.procs, server.sched)
                        if prog else "").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_form_multi(self):
                """Every field with all its values — day checkboxes repeat a name."""
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                return parse_qs(body)

            def _read_form(self, raw=None):
                raw = self._read_form_multi() if raw is None else raw
                return {k: v[0] for k, v in raw.items()}

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    body = _page(server.config, server.procs,
                                 server.sched).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/status":
                    status = {p.id: server.procs.is_running(p) for p in server.config.programs}
                    body = json.dumps(status).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/card":
                    # One program's card, for refreshing it in place.
                    qs = parse_qs(parsed.query)
                    prog = server.config.get(qs.get("id", [""])[0])
                    if prog is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    body = _card(prog, server.procs, server.sched).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
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
                            url=form.get("url", "").strip(),
                            probe=form.get("probe", "").strip(),
                            auto_launch=bool(form.get("auto")),
                        ))
                    self._redirect_home()
                elif self.path == "/action":
                    raw = self._read_form_multi()
                    form = self._read_form(raw)
                    prog = server.config.get(form.get("id", ""))
                    action = form.get("action")
                    if prog:
                        if action == "launch":
                            server.procs.launch(prog)
                        elif action == "stop":
                            server.procs.stop(prog)
                        elif action == "restart":
                            server.procs.restart(prog)
                        elif action == "edit":
                            # Shared Program instance: the menu bar picks the
                            # change up on its next refresh tick. Name and
                            # target are required, so a blank field keeps the
                            # current value rather than emptying the entry.
                            name = form.get("name", "").strip()
                            target = form.get("target", "").strip()
                            if name:
                                prog.name = name
                            if target:
                                prog.target = target
                            prog.kind = (KIND_PYTHON if form.get("kind") == KIND_PYTHON
                                         else KIND_SHELL)
                            prog.args = form.get("args", "").strip()
                            prog.cwd = form.get("cwd", "").strip()
                            prog.url = form.get("url", "").strip()
                            prog.probe = form.get("probe", "").strip()
                            server.config.save()
                        elif action == "set_auto":
                            # Unchecked boxes aren't submitted at all.
                            prog.auto_launch = bool(form.get("auto"))
                            server.config.save()
                        elif action == "add_schedule":
                            prog.schedules.append(_schedule_from_form(form, raw))
                            server.config.save()
                        elif action == "save_schedule":
                            rule = prog.get_schedule(form.get("sid", ""))
                            if rule:
                                _apply_schedule_form(rule, form, raw)
                                server.config.save()
                        elif action == "remove_schedule":
                            rule = prog.get_schedule(form.get("sid", ""))
                            if rule:
                                prog.schedules.remove(rule)
                                server.config.save()
                        elif action == "remove":
                            server.procs.stop(prog)
                            server.config.remove(prog.id)
                    self._done(form.get("id", ""))
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
