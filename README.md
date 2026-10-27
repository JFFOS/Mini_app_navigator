# Mini App Navigator

A lightweight macOS **menu bar app** to launch and manage your "mini programs" —
small Python scripts or shell commands — from one place.

Each registered program gets a submenu with **Launch / Stop / View Log** actions
and a checkmark while it's running. Adding, removing, and full management happen
in a local web dashboard opened from the menu, split into three tabs —
**Programs**, **Groups** and **Schedules**.

## Features

- 🚀 Menu bar launcher for Python scripts and shell commands
- ▶️ Launch / stop programs and see live running status
- 📄 Per-program log files with a live-tailing web log viewer
- 🔗 Optional dashboard URL per program — an **Open** button (menu bar and web
  dashboard) opens the program's own web UI in your default browser
- 🩺 Optional **liveness probe** per program, for commands that only *launch*
  something and exit — see below
- ✏️ **Edit** button on every entry — change name, kind, command/script,
  arguments, working dir, dashboard URL and probe in place
- ⚡️ Per-program **Auto launch** toggle (off by default) — flagged programs
  start with the navigator
- ⏰ Per-program **schedule** — launch and stop at set times, daily, on weekdays,
  on weekends, or on any weekdays you pick — see below
- 🗂 **Groups** — collapse a set of programs into one menu bar entry that
  launches, stops and restarts all of them together, and hide a whole group
  from the menu when you only manage it from the dashboard — see below
- 🌐 Local web dashboard (stdlib only), tabbed into Programs / Groups / Schedules
- 🔁 Launch-at-login toggle
- 💤 On-demand loading — the dashboard web server starts only when opened,
  keeping idle RAM low
- 🧷 Programs keep running when you quit the app, and are re-adopted on relaunch

## The dashboard

The dashboard is three tabs, one per question you might be asking:

| Tab | Answers |
|---|---|
| **Programs** | what each program *is* — launch/stop/restart, edit, logs, and adding new ones |
| **Groups** | what belongs with what — membership, bulk launch/stop/restart, show or hide in the menu |
| **Schedules** | when things run — every program's timed rules, all on one screen |

Nothing is duplicated between them: per-program buttons live on Programs only,
so there is never a second place to press the same thing. A program's card shows
which group it is in as a chip that links to it on the Groups tab, and its
schedule as a one-line digest with a **⏰ Schedule** link across to its rules.

## Groups

A group is a named set of programs that belong together — a "morning stack", a
set of services that only make sense running side by side. Create one under
**Add group** on the **Groups** tab, then press **Edit group** and tick the
programs that belong in it.

Membership is picked from the group, not one program at a time: choosing a
group's contents is one decision about one group, and the picker is the screen
where the whole set can be seen at once. A program belongs to exactly one group,
so ticking one that currently sits in another *moves* it — the picker says which
group that is before you do.

In the **menu bar** a grouped program is no longer listed on its own: the group
takes its place as a single entry, with

- **Launch All / Stop All / Restart All** for everything in it at once,
- a checkmark when every member is running and a dash when only some are,
- an `N of M running` line, and
- each member still there as a submenu below, so nothing is lost by grouping.

On the **Groups** tab each group is a section listing its members as compact
rows, with the same three bulk buttons and a live `N of M running` count.
**Edit group** renames it, sets its membership, or deletes it — deleting keeps
the programs, they simply go back to being listed on their own. Programs in no
group are gathered under **Ungrouped**.

### Show or hide in the menu

Every group has a **Show in menu** checkbox in its dashboard header. Turn it off
and the group *and its programs* disappear from the menu bar entirely, while
staying fully manageable — and fully scheduled — from the dashboard. That keeps
the menu to the things you actually launch by hand, without ungrouping or
deleting anything.

## Schedules

Each program can carry any number of timed rules, and the **Schedules** tab
shows all of them on one screen — every program, every rule, open at once, which
is how a set of timings is actually read. (A program's card links across with
**⏰ Schedule**.) A rule is an **action**, a **time**, and the **days** it runs
on:

| Action | Fires |
|---|---|
| **Launch & stop** | starts at the first time, stops at the second — one rule for the whole day's window |
| **Launch only** | starts at the time |
| **Stop only** | stops at the time |

A window whose stop time is at or before its start reads as overnight, so
*22:00 → 06:00* stops the following morning — shown as **next day** on the rule.

Days are picked as seven checkboxes with one-click presets:

| Preset | Days |
|---|---|
| Daily | every day |
| Weekdays | Mon–Fri |
| Weekend | Sat, Sun |
| *(pick your own)* | e.g. Mon, Wed, Fri — or a single day for a weekly rule |

So a typical working day is one rule: *Run 09:00 → 18:30 · weekdays*. The
program's card digests its rules to one line, and its menu bar submenu shows an
**⏰ Next:** line.

Times are local, and rules survive sleep sensibly: a slot missed because the Mac
was asleep or the navigator was restarting still fires up to **15 minutes** late,
and is skipped after that rather than running at a surprising hour. Each end of
a rule is remembered separately in `~/.mini_app_navigator/schedule_state.json`,
so restarting inside that window doesn't repeat one, and a missed launch doesn't
swallow its stop. Every firing is written to the program's own log.

A rule with no days ticked, or an unusable time, is kept but marked **never
fires** rather than being silently dropped.

To pause every rule at once — for a holiday, say — click **Schedules: On** in
the menu bar menu; it then reads **Schedules: Off (all paused)** until clicked
again, and the Schedules tab says which state you're in. Rules are left as they are; slots that come due while paused
are skipped (and logged as such) rather than fired later when you turn it back on.

## Liveness probes

Status is normally tracked by the PID of the command that was launched. That
breaks for a target that is only a *launcher* — `open -a Stats.app`, or a script
ending in `nohup … &` — because it hands off to a detached process and exits
immediately. The navigator would show **stopped** while the program is running,
re-launch it on every start, and have nothing to Stop.

Give those entries a **liveness probe** and the navigator will find the real
process, adopt it, and manage it normally:

| Probe | Finds the program by |
|---|---|
| *(empty)* | the port of the program's dashboard URL, if that URL is a local one |
| `8787` (or `port:8787`) | a process listening on that local port |
| `match:shortcutguard_app.py` | `pgrep -f` against the full command line |

So a program with a `http://127.0.0.1:8787/` dashboard URL needs no probe at
all — the URL is the probe. Fill the field in only to override that, or for a
program with no local web UI.

Adopted processes are shown with a **via probe** badge in the dashboard, and are
stopped on their own (not as a process group), since the navigator didn't create
that group.

## Requirements

- macOS
- Python 3.8+
- [`rumps`](https://pypi.org/project/rumps/)

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python mini_app_navigator.py
```

Or use the included **Start**, **Stop**, and **Restart Mini App
Navigator.command** launchers.

Configuration and logs live under `~/.mini_app_navigator/`.

## License

[MIT](LICENSE)
