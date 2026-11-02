"""Compatibility entry point; implementation lives in the shared skills directory."""
from pathlib import Path

_shared = Path(__file__).resolve().parents[4] / "skills" / 'tapd-config-init/scripts/init_tapd_config.py'
# Execute in this module so imports and command-line calls retain their public API.
__file__ = str(_shared)
exec(compile(_shared.read_bytes(), str(_shared), "exec"), globals())
