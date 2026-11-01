"""`hermes omo world`: generate the Hermes HQ datapack.

The plugin's top-level cli.py calls add_parser(subparsers) and run(args).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import datapack as dp
from . import inventory as inv
from . import layout as lay

DEFAULT_OUT = "hermes-hq-datapack"
DEFAULT_PORT = 8780


def _default_port():
    try:
        v = int(os.environ.get("OMO_PORT", "") or DEFAULT_PORT)
    except ValueError:
        v = DEFAULT_PORT
    return v


def add_parser(subparsers):
    p = subparsers.add_parser(
        "world",
        help="generate a Minecraft datapack (Hermes HQ) with one Hermes desk per profile",
        description="Inventory this Hermes install (names, models, counts only; never secrets) and write a "
                    "Minecraft 26.2 datapack for Omo Space that builds 'Hermes HQ': one preset Hermes Computer "
                    "desk per profile plus a Hermes Board web wall.",
    )
    p.add_argument("--out", default=None, help=f"output dir (default ./{DEFAULT_OUT}, or ./{DEFAULT_OUT}.zip with --zip)")
    p.add_argument("--zip", action="store_true", help="write a .zip instead of a folder")
    p.add_argument("--force", action="store_true", help="replace an existing datapack at --out")
    p.add_argument("--namespace", default="hermes_hq", help="datapack namespace (a-z 0-9 _; default hermes_hq)")
    p.add_argument("--bridge-port", type=int, default=None,
                   help=f"Omo bridge port for the Board wall (default $OMO_PORT or {DEFAULT_PORT})")
    p.add_argument("--profiles", default=None, help="only these profiles (comma separated; 'default' = root)")
    p.add_argument("--exclude", default=None, help="skip these profiles (comma separated)")
    p.add_argument("--max-desks", type=int, default=48, help="at most this many desks (default 48)")
    p.add_argument("--hermes-root", default=None, help="Hermes root (default $HERMES_ROOT or ~/.hermes)")
    p.add_argument("--dry-run", action="store_true", help="print the inventory and layout; write nothing")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(omo_world_run=run)
    return p


def _show(s):
    """A name for a terminal: printable, control chars escaped."""
    return s if s.isprintable() else repr(s)


def _home_short(path):
    try:
        return "~/" + str(Path(path).resolve().relative_to(Path.home().resolve()))
    except (ValueError, OSError):
        return str(path)


def run(args):
    out = sys.stdout
    err = sys.stderr
    ns = args.namespace or ""
    if not dp.NAMESPACE_RE.match(ns) or ns in dp.RESERVED_NAMESPACES:
        print(f"error: bad --namespace {ns!r}: use 1-48 characters of a-z, 0-9 and _ "
              f"(not {', '.join(sorted(dp.RESERVED_NAMESPACES))})", file=err)
        return 2
    port = args.bridge_port if args.bridge_port is not None else _default_port()
    if not 1 <= port <= 65535:
        print(f"error: bad --bridge-port {port}", file=err)
        return 2
    if args.max_desks < 1:
        print("error: --max-desks must be at least 1", file=err)
        return 2
    max_desks = min(args.max_desks, lay.HARD_MAX_DESKS)
    root = Path(args.hermes_root).expanduser() if args.hermes_root else inv.hermes_root()
    data = inv.collect(root, only=args.profiles, exclude=args.exclude)
    profiles = data["profiles"]
    if not profiles:
        print(f"error: no Hermes profiles found under {_home_short(root)}"
              + (" matching --profiles/--exclude" if args.profiles or args.exclude else ""), file=err)
        return 1
    shown = profiles[:max_desks]
    layout = lay.plan_layout(len(shown))
    url = f"http://127.0.0.1:{port}/board"
    summary = {
        "inventory": dict(data, root=_home_short(root)),
        "layout": layout,
        "namespace": ns,
        "board_url": url,
        "pack_format": dp.PACK_FORMAT,
        "not_built": [p["name"] for p in profiles[max_desks:]],
    }
    try:
        files = dp.build_files(data, namespace=ns, bridge_port=port, max_desks=max_desks)
    except ValueError as e:
        print(f"error: {e}", file=err)
        return 2

    if args.dry_run:
        if args.json:
            json.dump(summary, out, indent=1, ensure_ascii=True)
            out.write("\n")
        else:
            _print_human(summary, out)
        return 0

    target = args.out or (DEFAULT_OUT + (".zip" if args.zip else ""))
    try:
        path = dp.write_output(files, target, as_zip=args.zip, force=args.force)
    except dp.OutputError as e:
        print(f"error: {e}", file=err)
        return 1
    except OSError as e:
        print(f"error: cannot write {target}: {e}", file=err)
        return 1
    summary["out"] = str(path.resolve())
    summary["files"] = len(files)
    if args.json:
        json.dump({k: summary[k] for k in ("out", "files", "namespace", "board_url", "pack_format", "not_built")}
                  | {"desks": layout["desk_count"], "size": layout["size"]}, out, indent=1, ensure_ascii=True)
        out.write("\n")
    else:
        _print_human(summary, out)
        _print_install(summary, path, args.zip, out)
    return 0


def _print_human(s, out):
    inv_ = s["inventory"]
    L = s["layout"]
    w, h, d = L["size"]
    print(f"Hermes root: {_show(inv_['root'])}", file=out)
    print(f"{len(inv_['profiles'])} profiles:", file=out)
    for p in inv_["profiles"]:
        plats = ", ".join(dp.clean(x, 32) for x in p["platforms"]) or "-"
        label = f" - {dp.clean(p['label'], 60)}" if p["label"] else ""
        print(f"  {_show(p['name'])}{label}", file=out)
        print(f"      model {dp.clean(p['model'], 60) or '(profile default)'} | {p['skills']} skills | "
              f"{p['cron_jobs']} cron jobs | platforms {plats} | {p['plugins']} plugins", file=out)
    for sk in inv_["skipped"]:
        print(f"  skipped {_show(sk['name'])}: {sk['reason']}", file=out)
    for m in inv_["missing"]:
        print(f"  not found: {_show(m)}", file=out)
    if s["not_built"]:
        print(f"  {len(s['not_built'])} profiles over --max-desks are not built", file=out)
    print(f"Hermes HQ: {L['desk_count']} desks in {L['cols']} columns x {L['rows']} rows, "
          f"hall {w} x {h} x {d} (W x H x D) toward +Z; Board wall -> {s['board_url']}", file=out)


def _print_install(s, path, as_zip, out):
    ns = s["namespace"]
    print(f"\nWrote {s['files']} files: {path.resolve()}", file=out)
    what = "the .zip" if as_zip else "the folder"
    print(f"""
Install (Omo Space, Minecraft 26.2):
  1. Copy {what} into your world's datapacks folder:
       cp -r "{path.resolve()}" "<world>/datapacks/"
     (Omo Space worlds: ~/.local/share/omo/runtime/game/saves/<World>/datapacks/)
  2. In game: /reload
  3. Stand in an open, flat area. The hall is built in front of you toward +Z (south),
     {s['layout']['size'][0]} x {s['layout']['size'][1]} x {s['layout']['size'][2]} blocks; everything in that box is replaced.
  4. /function {ns}:preview    (particle outline only, nothing changes)
  5. /function {ns}:build
  Also: /function {ns}:info  (summary in chat), /function {ns}:clear  (remove it again).
Each desk makes one Omo workspace the first time it is opened (the bridge allows 128 in total).
The Board wall shows {s['board_url']}; the Omo bridge must be running on that port.""", file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hermes omo")
    sub = parser.add_subparsers(dest="omo_command", required=True)
    add_parser(sub)
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
