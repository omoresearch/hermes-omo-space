"""Test bootstrap: import the plugin directory as the package ``omo_space_plugin``.

The Hermes loader imports a directory plugin as a package (``hermes_plugins.<slug>``) so the
relative imports in ``__init__.py`` resolve; the tests mirror that under a stable alias. Hermes core
is NOT required: every Hermes import in the plugin is lazy with a stdlib fallback, so the suite runs
under any Python 3.11+ that has pytest.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

PACKAGE = "omo_space_plugin"
_ROOT = Path(__file__).resolve().parents[1]

# ``python -m pytest`` from the repo root puts the root on sys.path, where this plugin's modules
# (cli.py, tools.py, ...) could shadow unrelated top-level modules.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _ROOT]


def _load_plugin_package():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_load_plugin_package()


class _RepoRootIsNotATestPackage:
    """The repo root *is* the plugin package (an ``__init__.py`` with relative imports), so pytest
    would import it standalone during collection, where relative imports cannot resolve. Collect
    the root as a plain directory instead."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == _ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("omo-space-repo-root"):
        config.pluginmanager.register(_RepoRootIsNotATestPackage(), "omo-space-repo-root")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own HOME and HERMES_HOME; nothing touches the real ones."""
    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    hermes_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    for name in ("OMO_WORKSPACE", "OMO_TAB", "OMO_HERMES_TITLE", "OMO_PORT"):
        monkeypatch.delenv(name, raising=False)
    # The port-ownership check reads /proc/net; tests that want it point it at a fake dir.
    monkeypatch.setattr(sys.modules[PACKAGE].tools, "PROC_NET", str(tmp_path / "no-proc-net"))
    yield hermes_home


@pytest.fixture
def hermes_home(_isolated_home):
    return _isolated_home


@pytest.fixture
def plugin():
    return sys.modules[PACKAGE]


class FakeState:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


class FakeCtx:
    """Records every register_* call. Optional surfaces (state, get_config, profile_name) are only
    present when passed, so tests can model an old or minimal Hermes."""

    def __init__(self, *, settings=None, data_dir=None, profile=None, minimal=False):
        self.tools, self.hooks, self.commands, self.cli = [], [], [], []
        self._settings = settings or {}
        if not minimal:
            self.get_config = self._get_config
            if data_dir is not None:
                self.state = FakeState(Path(data_dir))
            if profile is not None:
                self.profile_name = profile

    def _get_config(self, key, default=None):
        return self._settings.get(key, default)

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_command(self, name, handler, description="", **kwargs):
        self.commands.append({"name": name, "handler": handler, "description": description, **kwargs})

    def register_cli_command(self, **kwargs):
        self.cli.append(kwargs)

    def hook(self, name):
        return [cb for n, cb in self.hooks if n == name]


@pytest.fixture
def fake_ctx_cls():
    return FakeCtx


class _BridgeHandler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802 - http.server API
        body = self.server.routes.get(self.path.split("?")[0])
        self.server.hits.append(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


SAMPLE_BOARD = {
    "version": "9.9.9", "tmux": True, "uptime": 12, "screens": 1,
    "workspaces": [
        {"name": "Lab", "kind": "terminal", "state": "live", "profile": "default",
         "tabs": [{"name": "t1", "kind": "hermes", "state": "live", "busy": True, "command": "hermes"},
                  {"name": "t2", "kind": "terminal", "state": "live", "busy": False, "command": "bash"}]},
        {"name": "Old", "kind": "terminal", "state": "stopped", "profile": "default", "tabs": []},
    ],
    "hermes": {"sessions": [{"session_id": "s-main", "omo": {"workspace": "ws1", "tab": "tab1", "title": "Lab"},
                             "workspace": "Lab", "elsewhere": False, "state": "tool"}],
               "events": []},
}


@pytest.fixture
def fake_bridge():
    """A loopback HTTP server pretending to be the Omo bridge, on an ephemeral port."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _BridgeHandler)
    server.routes = {"/health": {"ok": True, "version": "9.9.9", "tmux": True, "workspaces": 2, "views": 1},
                     "/board.json": SAMPLE_BOARD}
    server.hits = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
