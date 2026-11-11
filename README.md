# Omo Space for Hermes (`omo-space`)

**Your whole Hermes system, as a building in Minecraft.** Every Hermes profile gets a desk in a
generated *Hermes HQ*, a glowing cavern hall (pond, waterfalls, glow tree, a big HERMES sign over the arch)
whose themed bays group profiles that share a prefix (`omo-*`, `yt-*` ...) into neighbourhoods, inside
[Omo Space](https://github.com/omoresearch/omo-space) (the Omo Research Minecraft world with Omo Computers,
terminals and web walls). Each desk is an **Omo Computer** set to that profile, and the **Hermes Board** on the
wall shows what every session is doing: CLI, TUI, Telegram and other gateway chats, cron jobs, subagents and
kanban workers.

> **Status.** Desks, status lamps and the Board wall work with Omo Space v1.3 today. Hermes activity on the
> Board, the *Elsewhere* list and HUD notices need the feed-aware bridge from
> [omoresearch/omo-space PR #40](https://github.com/omoresearch/omo-space/pull/40), **coming in the next Omo
> Space release**. The plugin already writes the feed that bridge reads.

<!-- Screenshots: real in-game shots are being captured to /data/yifan/hermes-omo-plugin/e2e/shots/;
     copy the chosen ones to docs/ in this repo and pin them in the catalog entry. -->
| Hermes HQ, one desk per profile | The Hermes Board wall |
|---|---|
| ![Hermes HQ hall](docs/hermes-hq-hall.png) | ![Hermes Board](docs/hermes-board.png) |

## Quickstart

```bash
hermes plugins install omoresearch/hermes-omo-space --enable
hermes omo
```

`hermes omo` asks one thing: **what kind of world do you want?** Type anything ("a cosy library in the
snow", "neon night city") or just press Enter and Hermes gets creative. Your own model then designs the
world from your profiles: a name, a look, and rooms named for what your agents actually do (an *Edit Bay*,
a *Research Den*, a *Watch Tower*), every profile at a desk with a wall of screens.

```
  ◆ Snowbound Stacks   Snow outside, lamplight within: every agent at its desk.
    Old Library · snowfield · night

    Edit Bay         9  Six editors, their boss and the Chinese localizer · floor3-editing-editor…
    Research Den    10  Scouting virals, ideas, briefs and scripts · floor3-idea-room, …
    Watch Tower      2  Keeping the machines and services healthy · floor5-agent-ops, …

    Enter build it   a another idea   q quit   or type what to change
```

Press Enter and it writes a new world. Open Omo Space → Singleplayer → your world, and the building rises
around you as you arrive. Right-click any desk to talk to that agent. The Hermes Board on the wall shows
what every session is doing.

Unattended: `hermes omo create "a forest studio" --yes` (or `--json`). No model? `--no-ai` plans rooms
from your profile names.

## Also included

- A **metadata-only activity feed** per profile (event kinds, tool names, timings; never message text,
  tool arguments or results) that the Omo bridge reads for the Board. Nothing leaves your machine.
- Agent tools `omo_status` / `omo_notify`, the `/omo` slash command and `hermes omo status` (a doctor).
- `hermes omo setup` (checklist across profiles) and `hermes omo world` (Hermes HQ for a world you
  already have).

## Install Omo Space (v1.3)

Two ways in (full steps in Omo Space's [README](https://github.com/omoresearch/omo-space#install)):

- **The Omo Space app** sets up its own Minecraft, Fabric, the mods, the bridge and its services.
- **CurseForge, the Modrinth App or Prism:** add *Omo Space*, *CC: Tweaked* and (for web walls) *MCEF
  Modern* to an instance, then install only the bridge (Linux and macOS):

  ```bash
  bash install.sh --bridge-only omo-space-1.3.0-linux-x64.tar.gz   # bridge + session server, links your instances
  omo-space-link --game-dir "<instance folder>"                    # an instance added later
  ```

Open a world once so the bridge is running. In the creative menu the desk block is the **Omo Computer**
(same id as the old Hermes Computer, `omolife:hermes_computer`), next to the **Omo Monitor**.

## Adding Hermes HQ to a world you already have

`hermes omo setup` walks a checklist and asks before it changes anything:

1. Omo Space data dir and bridge: found via `OMO_PORT`, the Omo launcher's service file, or ports
   8780-8789 that belong to **your** user. If this profile points elsewhere it offers
   `hermes -p <profile> config set plugins.entries.omo-space.settings.bridge_url http://127.0.0.1:<port>`.
2. Profiles: shows every profile and whether omo-space is enabled. `hermes omo setup --enable-all`
   lists `hermes -p <profile> plugins enable omo-space` for each one and runs them after one y/N prompt.
3. World: offers to generate Hermes HQ and copy it into `<world>/datapacks/hermes_hq/`. Then in game:
   `/reload`, stand in an open flat area, `/function hermes_hq:preview`, `/function hermes_hq:build`.
   Right-click each desk's Omo Computer once: since Omo Space v1.3 a station only opens its workspace
   after its owner uses it.

Without a terminal (cron, gateway, CI) `hermes omo setup` just prints the checklist and exits 0.
The same steps by hand:

```bash
hermes omo status                                  # doctor: bridge, feed, next step
hermes omo world --list-worlds
hermes omo world --install "Omo World"             # --force replaces an earlier Hermes HQ, nothing else
hermes omo world --install "My World" --saves-dir "<instance folder>/saves"   # store (CurseForge/Modrinth/Prism) worlds
hermes omo world --out ./hermes-hq-datapack        # or just write the datapack somewhere
```

Then chat with Hermes anywhere.

## What you see

- **Omo Computer desks**, one per profile, the profile name on a plate. Opening one starts a Hermes tab in
  that profile (`hermes -p <profile> chat --tui`), kept by Omo Space's tmux server when you walk away.
- **Status lamps**: each desk's lamp glows gold while its agent or a command works, green when idle. Omo
  Space drives these itself.
- **The Hermes Board** (`/board`, on the wall in the hall): every tab with what its agent is doing,
  `thinking`, `running terminal 12s`, `idle · 3 turns`, compact cards past 24 live workspaces. *(PR #40)*
- **Elsewhere**: sessions with no desk (a Telegram chat on the gateway, a cron job, a plain terminal, a
  subagent) listed with their platform. *(PR #40)*
- **HUD notices**: an agent's `omo_notify` shows as a card top-left for about 5 s, up to three, only while no
  screen is open; an open station panel gets a toast. `/omonotices on|off|test` in game, or
  `hermesNotices=false` in `config/omolife-client.properties`. *(PR #40)*
- **The station splash**: before a desk connects it shows the Omo Space title card with the omo.sh mark.
  *(omo-space PR #41, next release)*

*(PR #40)* = needs the bridge from omoresearch/omo-space PR #40, coming in the next Omo Space release.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `hermes omo status` says the bridge is **not yours** | On a shared machine port 8780 can be another user's Omo bridge; the plugin never queries it. Point this profile at yours: `hermes omo setup` finds it, or `hermes config set plugins.entries.omo-space.settings.bridge_url http://127.0.0.1:<port>`. |
| Board shows no Hermes activity, but `hermes omo feed` has events | Your Omo bridge predates Hermes feed support (`hermes omo status` says so). That support is in omoresearch/omo-space PR #40, coming in the next Omo Space release. |
| A desk says "Waiting for the world owner" | Omo Space v1.3: right-click the desk's Omo Computer (or its wall) once. |
| No notices in game | `/omonotices on`, then `/omonotices test`. They draw only while no screen is open, and need the PR #40 bridge. |
| Board shows activity but no messages | By design: `omo_notify` text stays off the wall Board unless the bridge service runs with `OMO_BOARD_MESSAGES=1`. |
| Board shows nothing and the feed is empty | The plugin is not enabled in that profile (`hermes omo setup` lists them), or `feed` is set to `false`. |
| Changes do not show up after updating the plugin | A running gateway keeps the plugin module it loaded. Run `hermes plugins disable omo-space` then `hermes plugins enable omo-space` (per profile) so it reloads. |
| `world --install` says the datapack already exists | Add `--force`; only `<world>/datapacks/hermes_hq/` is replaced. Run `/reload` in game afterwards. |
| Several worlds and `--install` refuses | Name one: `hermes omo world --install "<World>"` (`--list-worlds` shows them). |

## Reference

The plugin adds:

1. **A live activity feed.** Hooks write a small, metadata-only feed into this plugin's own data dir
   in the active profile. The feed-aware Omo bridge (omo-space PR #40) reads it to show what every
   agent is doing on the Hermes Board, list sessions that live outside the world (Telegram, cron, plain
   terminals, subagents) under *Elsewhere*, and turn `omo_notify` into HUD notices.
2. **Two agent tools** in toolset `omo`:
   - `omo_status`: read-only. Is Omo Space running, what is on the board, and where this conversation lives.
   - `omo_notify {text, level?}`: queue a short in-game notice (max 140 chars, redacted): a HUD card and
     a station toast.
3. **`/omo`**: a one-paragraph answer to "where is this conversation in the world?"
4. **`hermes omo`**:
   - `hermes omo` / `hermes omo create [WISH...] [--name] [--profiles|--exclude] [--no-ai] [--yes|--json]`:
     design a world with your model (via Hermes plugin LLM access: your active provider, host-owned auth)
     and write it as a new world folder with the Hermes HQ datapack inside. It never touches an existing world.
   - `hermes omo setup [--enable-all] [--non-interactive] [--json]`: the guided checklist above.
   - `hermes omo status [--json]`: a doctor. Checks the bridge on the configured URL, the feed dir and
     marker, the age of the last event, and which profiles on this machine have a feed, then gives one next step.
   - `hermes omo feed [-n N] [--json] [--follow]`: print or follow this profile's feed.
   - `hermes omo world [--install [WORLD] | --out DIR | --zip] [--list-worlds] [--dry-run] ...`:
     generate the *Hermes HQ* datapack (one Omo Computer desk per profile plus a Hermes Board wall).
     `--saves-dir` points `--install`/`--list-worlds` at a store instance's `saves/`.
     `--dry-run --json` prints the layout as data (bays, desks, stations, Board, regions); `worldgen.hall.Hall`
     gives the full block model for previews. In game the hall builds in a few staged ticks.

## Privacy and disclosure (what it does and does not do)

- **Network:** only plain-HTTP `GET /health` and `GET /board.json` to the Omo bridge on a
  **loopback** address (`127.0.0.1`, `localhost`, `::1`). Timeout 1.5 s. Environment proxies are
  ignored and redirects are refused, so nothing leaves the machine. A non-loopback `bridge_url`
  is refused. Hooks never touch the network. On Linux, before probing, the plugin reads
  `/proc/net/tcp` and `/proc/net/tcp6` to check that the process listening on the bridge port belongs
  to you; if another user's Omo bridge owns the port it is reported as "not yours" and never queried.
- **The world designer** (`hermes omo` / `create`, only when you run it) sends your wish plus each profile's
  name and its `profile.yaml` display name/description to **your own active model** through Hermes'
  `ctx.llm` (no keys handled by the plugin). Never chats, memories, config values or secrets. `--no-ai` skips it.
- **No telemetry, no accounts, no secrets.** Nothing is reported anywhere; there is nothing to opt into.
- **Reads outside its own dir:** `hermes omo setup` reads each profile's `plugins.enabled` /
  `plugins.disabled` and plugin dir names (to show where omo-space is on), the Omo launcher's service
  file (`omo-bridge.service` + drop-ins, or the launchd plist) for `OMO_PORT`, and lists world folders
  in the Omo saves dir. `hermes omo status` lists which profiles have an omo-space feed
  (globbing `<root>/profiles/*/plugin-data/*/feed/omo-feed.json`). `hermes omo world` (only when you
  run it) reads, for every profile under your Hermes root: `profile.yaml` (display name/description),
  `config.yaml` (only model name/provider, enabled platform names and enabled plugin names are kept),
  `cron/jobs.json` (job count only), plugin dir names and SKILL.md file names (never opened). It never
  opens `.env`, `auth.json`, `state.db`, logs or session files.
- **Writes:** only its own profile-scoped dir, `<HERMES_HOME>/plugin-data/<namespace>/feed/`
  (dir `0700`, files `0600` on Linux/macOS; on Windows the profile dir's per-user ACL applies).
  `hermes omo world` writes only to the output path you give it; `hermes omo world --install` writes
  only `<world>/datapacks/hermes_hq/` (refuses to replace it without `--force`, never touches any
  other datapack).
- **Runs commands only when you say yes:** `hermes omo setup` may run `hermes -p <profile> config set
  plugins.entries.omo-space.settings.bridge_url ...` and, with `--enable-all`, `hermes -p <profile>
  plugins enable omo-space` (or `plugins install ... --enable` where it is not installed yet), each
  batch only after a y/N prompt at an interactive terminal. Under cron, the gateway, a pipe or CI it
  only prints the checklist and exits 0; it never prompts or blocks. It never edits `config.yaml` itself.
- **Records metadata only:** event kinds, tool *names*, counters, durations, timestamps, model and
  platform names, API status codes, kanban task ids, and the three Omo ids `OMO_WORKSPACE`,
  `OMO_TAB` and `OMO_HERMES_TITLE`. Never message text, tool arguments, tool results, error
  messages, kanban summaries or subagent goals. The one exception is `omo_notify` text, which has
  control characters stripped, anything that looks like a secret masked, and is capped at 140 characters.
- **On the Board (bridge side, omo-space PR #40):** `/board.json` carries status only: per agent profile,
  platform, state, tool name, tool time, turns, age, workspace name and whether it is elsewhere. No session
  ids, pids, models, titles or tool history. The text of `omo_notify` messages is off the wall Board unless
  the bridge service runs with `OMO_BOARD_MESSAGES=1`. HUD notices draw only on your own screen
  (`/omonotices off` hides them, e.g. on stream). Nothing from the feed is written into world data.
- **Never:** reads secrets or credential files, spawns processes or threads, approves or blocks
  tools (`pre_tool_call` always returns `None`), injects context, patches Hermes, or touches a
  running game or bridge.
- **Cost:** every hook is a dict update plus at most one small atomic write and one append. Hooks
  catch every exception. After a filesystem error the writer pauses for 30 s instead of retrying on every call.

## Settings

Set these in `plugins.entries.omo-space.settings` (the Desktop settings form shows them too):

| key | type | default | meaning |
|---|---|---|---|
| `feed` | bool | `true` | Write the activity feed. |
| `bridge_url` | str | `""` | Empty means `http://127.0.0.1:$OMO_PORT`, or `8780` if `OMO_PORT` is unset. Loopback only. |
| `notify_on_finish` | bool | `false` | Queue a notice when a turn that took more than 60 s ends. |

Settings are read live: a `hermes config set plugins.entries.omo-space.settings.<key> ...` takes
effect on the next tool call, `/omo` or `hermes omo` run (hooks pick up `feed` /
`notify_on_finish` within 5 s), without restarting a long-lived plugin host.

## Feed format (v1)

The full contract is in `CONTRACT.md` next to this repo. In short:

- `omo-feed.json`: marker `{"v":1,"plugin":"omo-space","plugin_version","profile","updated"}`.
- `sessions/<session_id>.json`: one presence snapshot per session, replaced atomically and throttled
  to at most 4 writes/s unless the state changes. `ended` snapshots older than 24 h are pruned and the
  dir is capped at 200 files.
- `events.jsonl`: append-only, rotated to `events.1.jsonl` at 512 KiB (one generation kept).

How Hermes hooks map to the feed:

| Hermes hook | feed |
|---|---|
| `on_session_start` | `session_start`, state `idle` |
| `pre_llm_call` | `turn_start`, state `thinking` |
| `pre_tool_call` / `post_tool_call` | `tool_start` / `tool_end` (`tool`, `duration_ms`, `ok`) |
| `on_session_end` (fires after **every** turn) | `turn_end` (`ok`, `interrupted`, `duration_ms`), state `idle` (or `error`) |
| `on_session_finalize` (real teardown: `/new`, quit, gateway reset) | `session_end`, state `ended` |
| `subagent_start` / `subagent_stop` | child presence with `parent_session_id`; child `session_end` |
| `kanban_task_*` | `kanban_claimed/completed/blocked`, plus `kanban` on the process's active session |
| `post_compaction`, `api_request_error` | `compaction`, `api_error` (`status_code`) |

**Session identity in tool hooks.** Tool hooks receive `task_id` and, on current Hermes, also
`session_id`. In the interactive CLI and the gateway `task_id` equals the session id. In a
`delegate_task` child, `task_id` is the subagent id and `session_id` is the child agent's own
session. A one-shot run without a task id gets a fresh uuid per turn. The plugin therefore uses the
`session_id` kwarg and falls back to `task_id`.

Cron jobs never finalize their session, so their last snapshot stays `idle` until the bridge
treats it as stale (15 min, or when the pid is dead).

## Compatibility

- **Hermes:** `requires_hermes: ">=0.22.3"`, the first release with every hook the plugin uses
  (`post_compaction` arrived in v0.22.3; `kanban_task_*` in 0.18.0; `ctx.state` / `ctx.get_config` in
  0.20.1). Optional context APIs are still probed, so an older Hermes that ignores `requires_hermes`
  (it is enforced from 0.21.2) degrades instead of crashing.
- **Omo Space:** v1.3 for desks (the Omo Computer), lamps and the Board wall; the bridge from omo-space
  PR #40 (next release) for Board activity, Elsewhere and HUD notices. `tests/test_contract_pr40.py` reads
  this plugin's feed with that bridge's own reader.
- **OS:** Linux, macOS and Windows. The other-user port check reads `/proc/net` and only runs on
  Linux; elsewhere it is skipped. Omo data dirs follow Omo Space's `docs/PLATFORMS.md`:
  `~/.local/share/omo`, `~/Library/Application Support/OmoResearch`, `%LOCALAPPDATA%\OmoResearch`
  (`OMO_DATA` overrides). Stdlib only, Python 3.11+.

## Development

```bash
pip install -r tests/requirements.txt   # pytest only; the plugin itself is stdlib-only
python -m pytest -q
hermes plugins validate .
hermes plugins doctor . --ci
```

License: MIT, © Omo Research.
