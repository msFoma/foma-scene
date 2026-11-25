"""Blender add-on entry point, also used by the background integration harness."""
import os

import bpy
from bpy.app.handlers import persistent

from .bridge import Bridge
from .runtime import Runtime

bl_info = {
    "name": "Foma Scene · Blender MCP",
    "author": "Blender MCP contributors",
    "version": (0, 1, 0),
    "blender": (4, 3, 0),
    "location": "3D Viewport > Sidebar > Blender MCP",
    "description": "Connect an MCP client to this Blender session",
    "category": "Interface",
}

runtime = None
bridge = None


@persistent
def file_loaded(*_):
    if runtime:
        runtime.file_loaded()


def tick():
    interval = bridge.pump() if bridge else None
    return interval


def start(token=None, port=None, use_timer=True):
    global runtime, bridge
    if bridge:
        raise ValueError("Bridge is already running")
    runtime = Runtime()
    bridge = Bridge(runtime.dispatch, token or os.getenv("BLENDER_MCP_TOKEN", ""),
                    port if port is not None else int(os.getenv("BLENDER_MCP_BRIDGE_PORT", "8765")))
    bpy.app.handlers.load_post.append(file_loaded)
    if use_timer:
        bpy.app.timers.register(tick, persistent=True)
    return bridge


def stop():
    global runtime, bridge
    if not bridge:
        return
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    bpy.app.handlers.load_post.remove(file_loaded)
    bridge.close()
    runtime = bridge = None


class MCP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    token: bpy.props.StringProperty(
        name="Bridge Token", subtype="PASSWORD",
        description="Enter the same token in the MCP server environment")
    port: bpy.props.IntProperty(name="Bridge Port", default=8765, min=1024, max=65535)

    def draw(self, context):
        self.layout.prop(self, "token")
        self.layout.prop(self, "port")


class MCP_OT_toggle(bpy.types.Operator):
    bl_idname = "wm.blender_mcp_toggle"
    bl_label = "Connect / Disconnect"
    bl_description = "Start or stop the bridge for this Blender session"

    def execute(self, context):
        try:
            if bridge:
                stop()
            else:
                addon = context.preferences.addons.get(__package__)
                preferences = addon.preferences if addon else None
                port = os.getenv("BLENDER_MCP_BRIDGE_PORT")
                start(token=os.getenv("BLENDER_MCP_TOKEN") or (preferences.token if preferences else None),
                      port=int(port) if port is not None else (preferences.port if preferences else None))
        except (ValueError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MCP_OT_rollback(bpy.types.Operator):
    bl_idname = "wm.blender_mcp_rollback"
    bl_label = "Roll Back Last MCP Edit"

    @classmethod
    def poll(cls, context):
        return bool(bridge and runtime.history and context.active_object and context.active_object.select_get())

    def execute(self, context):
        from .runtime import key

        obj = context.active_object
        try:
            runtime.undo_last(runtime.token([obj]), key(obj))
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MCP_PT_sidebar(bpy.types.Panel):
    bl_label = "Foma Scene"
    bl_category = "Blender MCP"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    def draw(self, context):
        layout = self.layout
        layout.operator(MCP_OT_toggle.bl_idname, text="Disconnect" if bridge else "Connect")
        if not bridge:
            layout.label(text="Configure the token in Add-on Preferences")
        if bridge:
            layout.label(text=f"Loopback · {bridge.server.server_port}")
            if context.active_object:
                layout.label(text=f"Target · {context.active_object.name}")
            layout.label(text=runtime.last_message)
            layout.operator(MCP_OT_rollback.bl_idname)


CLASSES = (MCP_Preferences, MCP_OT_toggle, MCP_OT_rollback, MCP_PT_sidebar)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    stop()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
