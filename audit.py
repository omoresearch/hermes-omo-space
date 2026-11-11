"""The setup audit: look at how this Hermes install is actually used before designing a world for it.

``hermes omo`` (and the ``omo_audit_setup`` tool) run this first, show the person what was found, and
ask before anything is sent to their model. The audit is read-only and metadata-only. On top of the
worldgen inventory (profile.yaml label, model, enabled platforms, cron job count, plugin count) it adds:

  skill names      the names of the directories that hold a SKILL.md (files are never opened); only the
                   skills that set a profile apart are kept, the ones most profiles share are dropped
  usage            from ``<profile>/state.db``, opened READ-ONLY, one aggregate query over the sessions
                   table: how many sessions per source (cli, discord, cron, ...) and when the newest one
                   started. No message, title, prompt or any other column is read. Older Hermes without
                   that table: the newest mtime in ``<profile>/sessions/`` instead (``stat`` only).

It never opens .env, auth.json, memories, SOUL.md, logs or session files.

From those signals it works out the shape of the setup (one agent, a small team, a big team or a
fleet), which agents are quiet, which share a speciality, and which talk to people on chat platforms.
The designer gets exactly ``model_text(audit)``; ``hermes omo`` can print it before you say yes.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

QUIET_DAYS = 30                 # no session for this long = a quiet profile
SHARED_SKILL_RATIO = 0.5        # a skill in more than half of the profiles says nothing about any one
MAX_SKILLS_SHOWN = 6
MAX_SCAN = 50_000               # directory entries walked per profile, at most
CHAT_PLATFORMS = ("discord", "telegram", "whatsapp", "slack", "signal", "matrix", "email", "sms", "photon",
                  "imessage", "teams")
EXCLUDED = frozenset((".git", ".github", ".hub", ".archive", ".curator_backups", ".locks", ".venv", "venv",
                      "node_modules", "site-packages", "__pycache__", ".pytest_cache"))
SUPPORT = frozenset(("references", "templates", "assets", "scripts"))


# --------------------------------------------------------------------------- per-profile signals
def profile_dir(root: Path, name: str) -> Path:
    return root if name == "default" else root / "profiles" / name


def skill_names(home: Path) -> list[str]:
    """Names of the skill directories (the folder that holds a SKILL.md). SKILL.md is never opened."""
    base = home / "skills"
    if not base.is_dir():
        return []
    out, seen, n = [], set(), 0
    for cur, dirs, files in os.walk(base, followlinks=False):
        n += len(dirs) + len(files)
        if n > MAX_SCAN:
            break
        has = "SKILL.md" in files
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (has and d in SUPPORT))
        if has:
            name = Path(cur).name
            if name not in seen:
                seen.add(name)
                out.append(name)
    return sorted(out)


USAGE_SQL = "SELECT source, COUNT(*), MAX(started_at) FROM sessions GROUP BY source"


def usage(home: Path) -> dict:
    """{'sessions': {source: count}, 'last': newest start time or None}. Read-only, aggregates only."""
    db = home / "state.db"
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1.0)
            try:
                rows = con.execute(USAGE_SQL).fetchall()
            finally:
                con.close()
            counts = {str(src or "other"): int(n) for src, n, _ in rows}
            starts = [float(t) for _, _, t in rows if isinstance(t, (int, float))]
            return {"sessions": counts, "last": max(starts) if starts else None}
        except (sqlite3.Error, ValueError, TypeError):
            pass
    return {"sessions": {}, "last": last_active(home)}


def last_active(home: Path) -> Optional[float]:
    """Newest mtime in <profile>/sessions (stat only). None when the profile never had a session."""
    newest = None
    try:
        with os.scandir(home / "sessions") as it:
            for i, e in enumerate(it):
                if i > MAX_SCAN:
                    break
                try:
                    t = e.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                newest = t if newest is None or t > newest else newest
    except OSError:
        return None
    return newest


SURFACE = {"cli": "terminal", "tui": "terminal", "oneshot": "scripted runs", "cron": "schedules",
           "kanban": "task board", "api_server": "API"}


def surfaces(sessions: dict) -> list[str]:
    """Where a profile is actually used, busiest first, in plain words (chat platforms keep their name)."""
    out = []
    for src, _ in sorted(sessions.items(), key=lambda kv: -kv[1]):
        word = SURFACE.get(src, src)
        if word not in out:
            out.append(word)
    return out[:4]


# --------------------------------------------------------------------------- the whole setup
def shape_of(n: int) -> tuple[str, str]:
    """(key, plain words) for a setup of n profiles."""
    if n <= 1:
        return "solo", "one agent: a single cosy room"
    if n <= 6:
        return "small", "a small team: a few rooms, everyone close together"
    if n <= 30:
        return "team", "a team: rooms by department"
    return "fleet", "a fleet: departments, with quiet agents in their own corner"


def run(profiles: list[dict], root: Path, now: Optional[float] = None) -> dict:
    """Audit every profile the inventory found. Pure metadata; never raises for one bad profile."""
    now = time.time() if now is None else now
    rows = []
    for p in profiles:
        home = profile_dir(Path(root), p["name"])
        try:
            skills = skill_names(home)
        except OSError:
            skills = []
        u = usage(home)
        t = u["last"]
        days = None if t is None else max(0, int((now - t) // 86400))
        rows.append({"name": p["name"], "label": p.get("label") or "", "skills_all": skills,
                     "sessions": sum(u["sessions"].values()), "used_on": surfaces(u["sessions"]),
                     "platforms": [x for x in (p.get("platforms") or []) if x != "api_server"],
                     "cron_jobs": int(p.get("cron_jobs") or 0), "idle_days": days})
    freq = Counter(s for r in rows for s in set(r["skills_all"]))
    limit = max(2, int(len(rows) * SHARED_SKILL_RATIO)) if len(rows) > 2 else 10 ** 9
    for r in rows:
        own = [s for s in r["skills_all"] if freq[s] <= limit]
        own.sort(key=lambda s: (freq[s], s))           # rarest first: what makes this profile itself
        r["skills"] = own[:MAX_SKILLS_SHOWN]
        r["quiet"] = r["idle_days"] is None or r["idle_days"] >= QUIET_DAYS
        r["chat"] = [x for x in r["platforms"] if x in CHAT_PLATFORMS]
    shared = Counter(s for r in rows for s in r["skills"])
    clusters = [(s, c) for s, c in shared.most_common() if c >= 2][:6]
    shape, shape_words = shape_of(len(rows))
    quiet = [r["name"] for r in rows if r["quiet"]]
    return {
        "profiles": rows,
        "shape": shape,
        "shape_words": shape_words,
        "count": len(rows),
        "chat": [r["name"] for r in rows if r["chat"]],
        "platforms": sorted({x for r in rows for x in r["chat"]}),
        "scheduled": [r["name"] for r in rows if r["cron_jobs"]],
        "cron_total": sum(r["cron_jobs"] for r in rows),
        "quiet": quiet if len(quiet) < len(rows) else [],     # everyone quiet = a new setup, not an archive
        "no_label": [r["name"] for r in rows if not r["label"]],
        "clusters": clusters,
    }


# --------------------------------------------------------------------------- what people and the model see
def summary_lines(a: dict) -> list[str]:
    """The audit in plain words, a few lines (what ``hermes omo`` prints before asking)."""
    n = a["count"]
    out = [f"{n} profile{'s' if n != 1 else ''}: {a['shape_words']}."]
    if a["chat"]:
        out.append(f"{len(a['chat'])} talk to people on {', '.join(a['platforms'])}.")
    if a["scheduled"]:
        out.append(f"{len(a['scheduled'])} run scheduled jobs ({a['cron_total']} in all).")
    if a["quiet"]:
        out.append(f"{len(a['quiet'])} have been quiet for {QUIET_DAYS}+ days: they get a quiet corner.")
    if a["clusters"]:
        out.append("Shared specialities: " + ", ".join(f"{s} ({c})" for s, c in a["clusters"][:4]) + ".")
    if a["no_label"]:
        out.append(f"{len(a['no_label'])} have no description, so their name has to say what they do.")
    return out


def model_text(a: dict) -> str:
    """Exactly what the designer sends about the setup (with the wish). Names, descriptions and counts."""
    lines = [f"Setup: {a['count']} profiles, {a['shape']} ({a['shape_words']})."]
    for r in a["profiles"]:
        bits = []
        if r["label"]:
            bits.append(r["label"][:100])
        if r["chat"]:
            bits.append("talks on " + "/".join(r["chat"]))
        if r["cron_jobs"]:
            bits.append(f"{r['cron_jobs']} scheduled jobs")
        if r["skills"]:
            bits.append("skills: " + ", ".join(r["skills"]))
        if r["used_on"]:
            bits.append(f"{r['sessions']} sessions, mostly {r['used_on'][0]}")
        if r["idle_days"] is None:
            bits.append("never used")
        elif r["quiet"]:
            bits.append(f"quiet {r['idle_days']} days")
        lines.append(f"- {r['name']}" + (": " + " | ".join(bits) if bits else ""))
    return "\n".join(lines)


SENT = ("profile names, descriptions, chat platform names, scheduled job counts, skill names, session counts "
        "and days since last use")
NEVER = "chats, memories, SOUL files, config values, keys or any file contents"


def as_json(a: dict) -> dict:
    """The audit for the agent tool: summary, the per-profile signals and exactly what would be sent."""
    return {
        "summary": summary_lines(a),
        "shape": a["shape"],
        "profiles": [{k: r[k] for k in ("name", "label", "chat", "cron_jobs", "skills", "sessions", "used_on",
                                        "idle_days", "quiet")}
                     for r in a["profiles"]],
        "would_send_to_your_model": SENT,
        "never_sent": NEVER,
        "next": "Show the user this summary and ask before calling omo_create_world.",
    }


def by_name(a: Optional[dict]) -> dict[str, dict]:
    return {r["name"]: r for r in (a or {}).get("profiles", [])}


def quiet_names(a: Optional[dict]) -> list[str]:
    return list((a or {}).get("quiet") or [])


__all__ = ["run", "summary_lines", "model_text", "as_json", "shape_of", "skill_names", "last_active", "usage",
           "by_name", "quiet_names", "SENT", "NEVER", "QUIET_DAYS"]


def _debug(root: Any = None) -> None:  # pragma: no cover - manual check: python -m <pkg>.audit
    from .worldgen import inventory as inv
    data = inv.collect(root)
    a = run(data["profiles"], Path(data["root"]))
    print("\n".join(summary_lines(a)))
    print(model_text(a))
