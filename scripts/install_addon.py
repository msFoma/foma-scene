"""Run inside Blender to persist add-on installation and pair the stdio server."""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

import bpy

MODULE = "blender_bridge"


def install(archive, config):
    if bpy.app.version < (4, 3, 0):
        raise RuntimeError("Foma Scene requires Blender 4.3 or newer.")

    addon = bpy.context.preferences.addons.get(MODULE)
    existing = (addon.preferences.token, addon.preferences.port) if addon else None
    if "FINISHED" not in bpy.ops.preferences.addon_install(filepath=archive, overwrite=True):
        raise RuntimeError("Blender could not install the Foma Scene archive.")
    if "FINISHED" not in bpy.ops.preferences.addon_enable(module=MODULE):
        raise RuntimeError("Blender could not enable Foma Scene.")
    preferences = bpy.context.preferences.addons[MODULE].preferences
    if existing:
        preferences.token, preferences.port = existing
    if len(preferences.token) < 16:
        preferences.token = secrets.token_urlsafe(32)

    if "FINISHED" not in bpy.ops.wm.save_userpref():
        raise RuntimeError("Blender could not save the add-on preferences.")
    # mkstemp creates a user-only file on platforms with Unix permissions.
    descriptor, filename = tempfile.mkstemp(dir=config.parent, prefix=".bridge-")
    staged = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump({"token": preferences.token, "port": preferences.port}, file)
        staged.replace(config)
    finally:
        staged.unlink(missing_ok=True)


if __name__ == "__main__":
    archive, config = sys.argv[sys.argv.index("--") + 1:]
    install(archive, Path(config))
