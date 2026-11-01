"""Hermes HQ world generator: inventory a Hermes install (metadata only) and
write a Minecraft 26.2 datapack that builds one preset Hermes Computer desk per
profile plus a Hermes Board web wall.

Stdlib only. Never reads secrets (.env, auth.json, state.db, tokens): see
inventory.py for the exact files it opens.
"""

from . import cli, datapack, inventory, layout  # noqa: F401
from .inventory import collect, hermes_root  # noqa: F401
from .layout import plan_layout  # noqa: F401
from .datapack import PACK_FORMAT, build_files, write_output  # noqa: F401

__all__ = ["collect", "hermes_root", "plan_layout", "PACK_FORMAT", "build_files", "write_output"]
