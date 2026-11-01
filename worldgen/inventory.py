"""Read-only, metadata-only inventory of a Hermes install.

Files this module opens, and nothing else:
  <profile>/profile.yaml      description / display_name
  <profile>/config.yaml       model.default|model, model.provider, platforms.<name>.enabled,
                              plugins.enabled (only these keys are kept)
  <profile>/cron/jobs.json    the number of jobs
  <profile>/plugins/*/        directory names (plugin.yaml existence only)
  <profile>/skills/**         SKILL.md file *names* (never opened)
It never opens .env, auth.json, state.db, logs, sessions or any credential file.
"""
import json
import os
import re
from pathlib import Path

# The Omo bridge accepts exactly these profile names (omobridge.py PROFILE_RE).
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_PLATFORM_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}\Z")

# Mirrors hermes agent/skill_utils.py so counts match `hermes profile info`.
EXCLUDED_SKILL_DIRS = frozenset((
    ".git", ".github", ".hub", ".archive", ".curator_backups", ".locks",
    ".venv", "venv", "node_modules", "site-packages", "__pycache__",
    ".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
))
SKILL_SUPPORT_DIRS = frozenset(("references", "templates", "assets", "scripts"))

MAX_CONFIG_BYTES = 4 * 1024 * 1024
MAX_JOBS_BYTES = 8 * 1024 * 1024
MAX_WALK_ENTRIES = 200_000


# --------------------------------------------------------------------------- root
def hermes_root(env=None):
    """The Hermes root (the dir holding profiles/): $HERMES_ROOT, else derived from
    $HERMES_HOME (a profile home is <root>/profiles/<name>), else ~/.hermes."""
    env = os.environ if env is None else env
    if env.get("HERMES_ROOT"):
        return Path(env["HERMES_ROOT"]).expanduser()
    home = env.get("HERMES_HOME")
    if home:
        p = Path(home).expanduser()
        if p.parent.name == "profiles":
            return p.parent.parent
        return p
    return Path(env.get("HOME") or Path.home()).expanduser() / ".hermes"


# --------------------------------------------------------------------------- yaml
def _load_pyyaml(text):
    import yaml  # noqa: PLC0415  (optional)
    return yaml.safe_load(text)


def _load_ruamel(text):
    from ruamel.yaml import YAML  # noqa: PLC0415  (optional)
    return YAML(typ="safe", pure=True).load(text)


def _load_mini(text):
    return _parse_mini(text)


_BACKENDS = [_load_pyyaml, _load_ruamel, _load_mini]


def _scalar(s):
    s = s.strip()
    if not s:
        return None
    if s[0] in "\"'":
        q = s[0]
        end = s.find(q, 1)
        while q == '"' and end > 0 and s[end - 1] == "\\":
            end = s.find(q, end + 1)
        body = s[1:end if end > 0 else None]
        if q == "'":
            return body.replace("''", "'")
        try:
            return json.loads('"' + body + '"')
        except ValueError:
            return body
    s = _strip_comment(s)
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        out = {}
        for part in _split_flow(s[1:-1]):
            k, sep, v = part.partition(":")
            if sep:
                out[_scalar(k)] = _scalar(v)
        return out
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?\d*\.\d+([eE][-+]?\d+)?", s):
        return float(s)
    return s


def _strip_comment(s):
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s


def _split_flow(s):
    parts, depth, q, cur = [], 0, None, ""
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _split_key(line):
    """'key: value' -> (key, value) outside quotes, or None."""
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == ":" and (i + 1 == len(line) or line[i + 1] in " \t"):
            key = line[:i].strip()
            if key[:1] in "\"'":
                key = _scalar(key)
            return key, line[i + 1:]
    return None


def _parse_mini(text):
    """A small YAML subset parser (block mappings, block lists, flow lists/maps,
    quoted scalars, comments). Best effort: anything odd is skipped, never raised."""
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.strip() in ("---", "..."):
            continue
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            continue
        ind = len(raw) - len(raw.lstrip(" "))
        lines.append((ind, raw.strip()))
    pos = 0

    def block(ind):
        nonlocal pos
        if pos >= len(lines):
            return None
        if lines[pos][1].startswith("- ") or lines[pos][1] == "-":
            return seq(lines[pos][0])
        return mapping(lines[pos][0])

    def mapping(ind):
        nonlocal pos
        out = {}
        while pos < len(lines) and lines[pos][0] == ind and not lines[pos][1].startswith("- "):
            kv = _split_key(lines[pos][1])
            pos += 1
            if kv is None:
                continue
            k, v = kv
            v = _strip_comment(v).strip() if not v.strip()[:1] in "\"'" else v.strip()
            if v in ("", "|", ">", "|-", ">-"):
                if pos < len(lines) and (lines[pos][0] > ind or
                                         (lines[pos][0] == ind and lines[pos][1].startswith("- "))):
                    if v and v[0] in "|>":
                        parts = []
                        base = lines[pos][0]
                        while pos < len(lines) and lines[pos][0] >= base:
                            parts.append(lines[pos][1])
                            pos += 1
                        out[k] = ("\n" if v[0] == "|" else " ").join(parts)
                    else:
                        out[k] = block(lines[pos][0])
                else:
                    out[k] = None
            else:
                out[k] = _scalar(v)
            while pos < len(lines) and lines[pos][0] > ind:       # junk deeper than expected
                pos += 1
        return out

    def seq(ind):
        nonlocal pos
        out = []
        while pos < len(lines) and lines[pos][0] == ind and (lines[pos][1].startswith("- ") or lines[pos][1] == "-"):
            item = lines[pos][1][1:].strip()
            if not item:
                pos += 1
                out.append(block(lines[pos][0]) if pos < len(lines) and lines[pos][0] > ind else None)
                continue
            kv = _split_key(item)
            if kv and not item[:1] in "\"'[{":
                # "- key: v" starts a mapping whose other keys sit at ind + 2
                lines[pos] = (ind + 2, item)
                out.append(mapping(ind + 2))
            else:
                pos += 1
                out.append(_scalar(item))
        return out

    try:
        result = block(0)
    except (IndexError, ValueError, RecursionError):
        return {}
    return result if isinstance(result, dict) else {}


def load_yaml(path):
    """A dict from a YAML file, {} when missing, too big or unparsable. Never raises."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            return {}
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for backend in _BACKENDS:
        try:
            data = backend(text)
        except ImportError:
            continue
        except Exception:  # noqa: BLE001  a real parser said it is not YAML: treat as empty
            return {}
        return data if isinstance(data, dict) else {}
    return {}


# --------------------------------------------------------------------------- per profile
def _is_profile_dir(p):
    try:
        return p.is_dir() and not p.is_symlink() and ((p / "config.yaml").is_file() or (p / "state.db").exists())
    except OSError:
        return False


def count_skills(profile_dir):
    skills = profile_dir / "skills"
    if not skills.is_dir():
        return 0
    n, seen, entries = 0, set(), 0
    for root, dirs, files in os.walk(skills, followlinks=True):
        try:
            real = os.path.realpath(root)
        except OSError:
            dirs[:] = []
            continue
        if real in seen:                 # symlink loop
            dirs[:] = []
            continue
        seen.add(real)
        entries += len(dirs) + len(files)
        if entries > MAX_WALK_ENTRIES:
            break
        has = "SKILL.md" in files
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_SKILL_DIRS and not (has and d in SKILL_SUPPORT_DIRS))
        if has:
            n += 1
    return n


def count_cron_jobs(profile_dir):
    path = profile_dir / "cron" / "jobs.json"
    try:
        if not path.is_file() or path.stat().st_size > MAX_JOBS_BYTES:
            return 0
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return 0
    jobs = data.get("jobs") if isinstance(data, dict) else data
    return len(jobs) if isinstance(jobs, list) else 0


def _model(cfg):
    m = cfg.get("model")
    if isinstance(m, str):
        return m.strip() or None, None
    if isinstance(m, dict):
        name = m.get("default") or m.get("model")
        prov = m.get("provider")
        return (str(name).strip() or None) if name else None, (str(prov).strip() or None) if prov else None
    return None, None


def _platforms(cfg):
    pl = cfg.get("platforms")
    out = []
    if isinstance(pl, dict):
        for name, v in pl.items():
            on = v.get("enabled") if isinstance(v, dict) else v
            if on is True and isinstance(name, str) and _PLATFORM_RE.match(name):
                out.append(name)
    return sorted(set(out))


def _plugins(cfg, profile_dir):
    names = set()
    pg = cfg.get("plugins")
    if isinstance(pg, dict) and isinstance(pg.get("enabled"), list):
        names |= {str(x) for x in pg["enabled"] if isinstance(x, (str, int))}
    pdir = profile_dir / "plugins"
    try:
        for d in pdir.iterdir() if pdir.is_dir() else ():
            if d.is_dir() and ((d / "plugin.yaml").is_file() or (d / "__init__.py").is_file()):
                names.add(d.name)
    except OSError:
        pass
    return len(names)


def _label(profile_dir):
    meta = load_yaml(profile_dir / "profile.yaml")
    for key in ("display_name", "description"):
        v = meta.get(key)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return ""


def describe_profile(name, profile_dir):
    cfg = load_yaml(profile_dir / "config.yaml")
    model, provider = _model(cfg)
    return {
        "name": name,
        "label": _label(profile_dir),
        "model": model,
        "provider": provider,
        "skills": count_skills(profile_dir),
        "cron_jobs": count_cron_jobs(profile_dir),
        "platforms": _platforms(cfg),
        "plugins": _plugins(cfg, profile_dir),
    }


# --------------------------------------------------------------------------- whole install
def _split_names(v):
    if not v:
        return []
    if isinstance(v, str):
        v = [v]
    out = []
    for item in v:
        out += [s.strip() for s in str(item).split(",") if s.strip()]
    return out


def collect(root=None, only=None, exclude=None):
    """{'root', 'profiles': [...], 'skipped': [{name, reason}], 'missing': [names asked for but absent]}.

    Profiles: 'default' (the root, when it looks like a Hermes home) first, then
    root/profiles/* sorted. Names the Omo bridge would refuse are skipped.
    """
    root = Path(root) if root else hermes_root()
    only = _split_names(only)
    exclude = set(_split_names(exclude))
    found, skipped = [], []
    if (root / "config.yaml").is_file() or (root / "state.db").exists():
        found.append(("default", root))
    pdir = root / "profiles"
    try:
        entries = sorted(pdir.iterdir(), key=lambda p: p.name) if pdir.is_dir() else []
    except OSError:
        entries = []
    for d in entries:
        if not _is_profile_dir(d):
            continue
        if d.name == "default" or not PROFILE_RE.match(d.name):
            skipped.append({"name": d.name, "reason": "not a valid Hermes profile name"})
            continue
        found.append((d.name, d))
    names = [n for n, _ in found]
    if only:
        found = [(n, d) for n, d in found if n in only]
    found = [(n, d) for n, d in found if n not in exclude]
    profiles = [describe_profile(n, d) for n, d in found]
    return {
        "root": str(root),
        "profiles": profiles,
        "skipped": skipped,
        "missing": [n for n in only if n not in names],
    }
