"""Exercise Codex's local client without using a model, then try a real agent turn."""
import asyncio
import base64
import io
import json
import shutil
import sys

from PIL import Image

from smoke import ROOT


def config():
    return (
        "-c", f"mcp_servers.blender.command={json.dumps(sys.executable)}",
        "-c", 'mcp_servers.blender.args=["-m","blender_mcp.server"]',
        "-c", 'mcp_servers.blender.env_vars=["BLENDER_MCP_TOKEN","BLENDER_MCP_BRIDGE_PORT"]',
        "-c", "mcp_servers.blender.startup_timeout_sec=10",
    )


async def local_client(out, env, session_id):
    evidence = {}
    with (out / "codex-app-server.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec("codex", "app-server", *config(),
                env=env, cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                limit=8 * 1024 * 1024)
        sequence = 0

        async def request(method, params, notify=False):
            nonlocal sequence
            sequence += 1
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notify:
                message["id"] = sequence
            process.stdin.write(json.dumps(message).encode() + b"\n")
            await process.stdin.drain()
            if notify:
                return
            while line := await asyncio.wait_for(process.stdout.readline(), 15):
                reply = json.loads(line)
                if reply.get("id") == sequence:
                    if "error" in reply:
                        raise RuntimeError(f"{method}: {reply['error']['message']}")
                    return reply["result"]
            raise RuntimeError(f"{method}: Codex app-server exited")

        try:
            await request("initialize", {"clientInfo": {"name": "blender_mcp_acceptance", "version": "0.1"},
                                         "capabilities": {"experimentalApi": True}})
            await request("initialized", {}, notify=True)
            skills = await request("skills/list", {"cwds": [str(ROOT)], "forceReload": True})
            evidence["skill_paths"] = [item["path"] for group in skills["data"] for item in group["skills"]
                                       if item["name"] == "blender-mcp" and item["enabled"]]
            assert evidence["skill_paths"], "Codex did not discover the canonical skill"
            opened = await request("thread/start", {"cwd": str(ROOT), "ephemeral": True, "sandbox": "read-only"})
            thread_id = opened["thread"]["id"]
            inventory = await request("mcpServerStatus/list", {"serverName": "blender",
                                       "threadId": thread_id, "detail": "toolsAndAuthOnly"})
            entry = next((item for item in inventory["data"] if item["name"] == "blender"), {})
            evidence["tools"] = sorted(entry.get("tools", {}))
            assert {"inspect_scene", "observe"} <= set(evidence["tools"]), (
                entry.get("toolsError") or "Codex did not list the configured MCP server")
            response = await request("mcpServer/tool/call", {"threadId": thread_id, "server": "blender",
                                     "tool": "observe", "arguments": {"view": {"cameras": ["Case front"],
                                                                               "resolution_percent": 35}}})
            meta = json.loads(response["content"][0]["text"])
            assert meta["session_id"] == session_id and meta["state"] == "succeeded", meta
            image = next(block for block in response["content"] if block["type"] == "image")
            pixels = base64.b64decode(image["data"])
            with Image.open(io.BytesIO(pixels)) as frame:
                frame.load()
                evidence["image_size"] = list(frame.size)
            (out / "codex-app-server.png").write_bytes(pixels)
            evidence.update(session_id=session_id, image_bytes=len(pixels), status="passed")
        except (AssertionError, KeyError, RuntimeError, OSError, ValueError, TimeoutError) as exc:
            evidence.update(status="unverified", error=str(exc) or type(exc).__name__)
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.terminate()
                await process.wait()
    return evidence


async def agent_turn(out, env):
    output = out / "codex-cli.jsonl"
    args = ("codex", "exec", "--ephemeral", "--sandbox", "read-only", "--json", "--ignore-user-config",
            "--cd", str(ROOT), *config(),
            "Use the repository's $blender-mcp skill and only the Blender MCP tools. Inspect the scene "
            "and use observe to capture the Case front camera at 35 percent. Report the active object, "
            "session ID, and actual shape visible in the image. Do not use shell commands.")
    with output.open("wb") as log:
        process = await asyncio.create_subprocess_exec(*args, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                       stdout=log, stderr=log)
        try:
            await asyncio.wait_for(process.wait(), 80)
        except TimeoutError:
            process.terminate()
            await process.wait()
    hits, failure = [], None
    for line in output.read_text(errors="replace").splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        item = event.get("item", {})
        if item.get("type") == "mcp_tool_call" and event.get("type") == "item.completed":
            hits.append({"tool": item.get("tool"), "status": item.get("status")})
        if event.get("type") == "turn.failed":
            failure = event["error"]["message"]
    return {"exit_code": process.returncode, "tool_calls": hits, "transcript": output.name, "error": failure,
            "status": "passed" if {x["tool"] for x in hits if x["status"] == "completed"} >=
            {"inspect_scene", "observe"} else "unverified"}


async def check_codex(out, env, session_id):
    if not shutil.which("codex"):
        return {"status": "unavailable"}
    return {"local_client": await local_client(out, env, session_id),
            "agent_turn": await agent_turn(out, env)}
