# Omo Space for Hermes (`omo-space`)

See your whole Hermes system (every profile, CLI/TUI/gateway session, subagent, cron job and kanban
worker) as a place inside **Omo Space**, the Omo Research Minecraft world with Hermes Computers,
terminals and web walls.

The plugin adds four things:

1. **A live activity feed.** Hooks write a small, metadata-only feed into this plugin's own data dir
   in the active profile. The Omo bridge reads it to light the desk lamps, show what every agent is
   doing on the Hermes Board, and list sessions that live outside the world (Telegram, cron, plain
   terminals, subagents) under *Elsewhere*.
2. **Two agent tools** in toolset `omo`:
   - `omo_status`: read-only. Is Omo Space running, what is on the board, and where this conversation lives.
   - `omo_notify {text, level?}`: queue a short in-game toast (max 140 chars, redacted).
3. **`/omo`**: a one-paragraph answer to "where is this conversation in the world?"
4. **`hermes omo`**:
   - `hermes omo status`: a doctor. Checks the bridge on the configured URL, the feed dir and marker, the age of the last event, and which profiles on this machine have a feed, then gives one next step.
   - `hermes omo feed [-n N] [--json] [--follow]`: print or follow this profile's feed.
   - `hermes omo world`: generate the *Hermes HQ* datapack (one Hermes Computer desk per profile plus a Hermes Board wall). Needs the bundled `worldgen` package.

## What it does and does not do (disclosure)

- **Network:** only plain-HTTP `GET /health` and `GET /board.json` to the Omo bridge on a
  **loopback** address (`127.0.0.1`, `localhost`, `::1`). Timeout 1.5 s. Environment proxies are
  ignored and redirects are refused, so nothing leaves the machine. A non-loopback `bridge_url`
  is refused. Hooks never touch the network.
- **Writes:** only its own profile-scoped dir, `<HERMES_HOME>/plugin-data/<namespace>/feed/`
  (dir `0700`, files `0600`). `hermes omo world` writes only to the output path you give it.
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

## Development

```bash
pip install -r tests/requirements.txt   # pytest only; the plugin itself is stdlib-only
python -m pytest -q
hermes plugins validate .
hermes plugins doctor . --ci
```

License: MIT, © Omo Research.
