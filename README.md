# Omo Space for Hermes (`omo-space`)

**Your whole Hermes system, as a building in Minecraft.** Every Hermes profile gets a desk in a
generated *Hermes HQ*, a glowing cavern hall (pond, waterfalls, glow tree, a big HERMES sign over the arch)
whose themed bays group profiles that share a prefix (`omo-*`, `yt-*` ...) into neighbourhoods, inside [Omo Space](https://github.com/omoresearch/omo-space) (the Omo
Research Minecraft world with Hermes Computers, terminals and web walls). Desk lamps light up while an
agent thinks or runs a tool, and the in-game **Hermes Board** shows what every session is doing:
CLI, TUI, Telegram and other gateway chats, cron jobs, subagents and kanban workers.

<!-- Screenshots: real in-game shots are being captured to /data/yifan/hermes-omo-plugin/e2e/shots/;
     copy the chosen ones to docs/ in this repo and pin them in the catalog entry. -->
| Hermes HQ, one desk per profile | The Hermes Board wall |
|---|---|
| ![Hermes HQ hall](docs/hermes-hq-hall.png) | ![Hermes Board](docs/hermes-board.png) |

## What you get in 30 seconds

- `hermes omo setup` checks Omo Space, finds **your** bridge port, lists which profiles have the plugin
  on, prints the exact commands for the rest, and installs the Hermes HQ datapack into your world.
- A **metadata-only activity feed** per profile (event kinds, tool names, timings; never message text,
  tool arguments or results) that the Omo bridge reads. Nothing leaves your machine.
- Agent tools `omo_status` / `omo_notify`, the `/omo` slash command ("where does this chat live in
  the world?") and `hermes omo status` (a doctor with one next step).

## Quickstart (you already run Hermes)

Prerequisite: Omo Space is installed and you have opened a world once (so its bridge is running).

```bash
hermes plugins install https://github.com/omoresearch/hermes-omo-space --enable   # placeholder URL until published
hermes omo setup
```

`hermes omo setup` walks a checklist and asks before it changes anything:

1. Omo Space data dir and bridge: found via `OMO_PORT`, the Omo launcher's service file, or ports
   8780-8789 that belong to **your** user. If this profile points elsewhere it offers
   `hermes -p <profile> config set plugins.entries.omo-space.settings.bridge_url http://127.0.0.1:<port>`.
2. Profiles: shows every profile and whether omo-space is enabled. `hermes omo setup --enable-all`
   lists `hermes -p <profile> plugins enable omo-space` for each one and runs them after one y/N prompt.
3. World: offers to generate Hermes HQ and copy it into `<world>/datapacks/hermes_hq/`. Then in game:
   `/reload`, stand in an open flat area, `/function hermes_hq:preview`, `/function hermes_hq:build`.

Without a terminal (cron, gateway, CI) `hermes omo setup` just prints the checklist and exits 0.
The same steps by hand:

```bash
hermes omo status                                  # doctor: bridge, feed, next step
hermes omo world --list-worlds
hermes omo world --install "Omo World"             # --force replaces an earlier Hermes HQ, nothing else
hermes omo world --out ./hermes-hq-datapack        # or just write the datapack somewhere
```

Then chat with Hermes anywhere. Desks light up and the Board fills in.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `hermes omo status` says the bridge is **not yours** | On a shared machine port 8780 can be another user's Omo bridge; the plugin never queries it. Point this profile at yours: `hermes omo setup` finds it, or `hermes config set plugins.entries.omo-space.settings.bridge_url http://127.0.0.1:<port>`. |
| Board shows nothing, but `hermes omo feed` has events | Your Omo bridge predates Hermes feed support (`hermes omo status` says so). Update Omo Space with its launcher. |
| Board shows nothing and the feed is empty | The plugin is not enabled in that profile (`hermes omo setup` lists them), or `feed` is set to `false`. |
| Changes do not show up after updating the plugin | A running gateway keeps the plugin module it loaded. Run `hermes plugins disable omo-space` then `hermes plugins enable omo-space` (per profile) so it reloads. |
| `world --install` says the datapack already exists | Add `--force`; only `<world>/datapacks/hermes_hq/` is replaced. Run `/reload` in game afterwards. |
| Several worlds and `--install` refuses | Name one: `hermes omo world --install "<World>"` (`--list-worlds` shows them). |

## Reference

The plugin adds:

1. **A live activity feed.** Hooks write a small, metadata-only feed into this plugin's own data dir
   in the active profile. The Omo bridge reads it to light the desk lamps, show what every agent is
   doing on the Hermes Board, and list sessions that live outside the world (Telegram, cron, plain
   terminals, subagents) under *Elsewhere*.
2. **Two agent tools** in toolset `omo`:
   - `omo_status`: read-only. Is Omo Space running, what is on the board, and where this conversation lives.
   - `omo_notify {text, level?}`: queue a short in-game toast (max 140 chars, redacted).
3. **`/omo`**: a one-paragraph answer to "where is this conversation in the world?"
4. **`hermes omo`**:
   - `hermes omo setup [--enable-all] [--non-interactive] [--json]`: the guided checklist above.
   - `hermes omo status [--json]`: a doctor. Checks the bridge on the configured URL, the feed dir and
     marker, the age of the last event, and which profiles on this machine have a feed, then gives one next step.
   - `hermes omo feed [-n N] [--json] [--follow]`: print or follow this profile's feed.
   - `hermes omo world [--install [WORLD] | --out DIR | --zip] [--list-worlds] [--dry-run] ...`:
     generate the *Hermes HQ* datapack (one Hermes Computer desk per profile plus a Hermes Board wall).
     `--dry-run --json` prints the layout as data (bays, desks, stations, Board, regions); `worldgen.hall.Hall`
     gives the full block model for previews. In game the hall builds in a few staged ticks.

## Privacy and disclosure (what it does and does not do)

- **Network:** only plain-HTTP `GET /health` and `GET /board.json` to the Omo bridge on a
  **loopback** address (`127.0.0.1`, `localhost`, `::1`). Timeout 1.5 s. Environment proxies are
  ignored and redirects are refused, so nothing leaves the machine. A non-loopback `bridge_url`
  is refused. Hooks never touch the network. On Linux, before probing, the plugin reads
  `/proc/net/tcp` and `/proc/net/tcp6` to check that the process listening on the bridge port belongs
  to you; if another user's Omo bridge owns the port it is reported as "not yours" and never queried.
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
| `notify_on_finish` | bool | `false` | Queue a toast when a turn that took more than 60 s ends. |

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
