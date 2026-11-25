"""Check Claude Code's native MCP connection and a separate model-backed turn."""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from smoke import ROOT


async def command(executable, args, cwd, env, transcript, timeout=25):
    with transcript.open("wb") as log:
        process = await asyncio.create_subprocess_exec(executable, *args, cwd=cwd, env=env,
                                                       stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            return await asyncio.wait_for(process.wait(), timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return None


def model_evidence(log, exit_code):
    tools, results, errors = {}, set(), []
    for line in log.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():
                errors.append(line.strip())
            continue
        kind = event.get("type")
        content = event.get("message", {}).get("content")
        blocks = content if isinstance(content, list) else ()
        if kind == "assistant":
            for entry in blocks:
                if entry.get("type") == "tool_use" and entry["name"].startswith("mcp__blender__"):
                    tools[entry["id"]] = entry["name"].rsplit("__", 1)[-1]
        elif kind == "user":
            for entry in blocks:
                if entry.get("type") == "tool_result" and not entry.get("is_error"):
                    results.add(entry["tool_use_id"])
        elif kind == "result" and event.get("is_error"):
            errors.extend(event.get("errors") or [event.get("result") or event.get("subtype", "failed")])
    completed = sorted({tool for call_id, tool in tools.items() if call_id in results})
    return {
        "exit_code": exit_code, "tools_completed": completed, "transcript": log.name,
        "status": "passed" if exit_code == 0 and {"inspect_scene", "observe"} <= set(completed) else "unverified",
        "error": str(errors[-1])[:500] if errors else ("Timed out" if exit_code is None else None),
    }


async def check_claude(out, env, executable):
    executable = shutil.which(executable)
    if not executable:
        return {"status": "unavailable"}
    executable = str(Path(executable).resolve())
    mcp = {"type": "stdio", "command": sys.executable, "args": ["-m", "blender_mcp.server"],
           "env": {"PYTHONPATH": str(ROOT)}}
    with tempfile.TemporaryDirectory(prefix="claude-mcp-", dir=out) as folder:
        isolated = {**env, "CLAUDE_CONFIG_DIR": str(Path(folder) / "settings")}
        setup_log = out / "claude-mcp-add.log"
        code = await command(executable, ("mcp", "add-json", "--scope", "local", "blender", json.dumps(mcp)),
                             folder, isolated, setup_log)
        health_log = out / "claude-mcp-list.log"
        if code == 0:
            code = await command(executable, ("mcp", "list"), folder, isolated, health_log)
        connected = code == 0 and any(line.startswith("blender:") and "Connected" in line
                                      for line in health_log.read_text(errors="replace").splitlines())
        client = {"status": "passed" if connected else "unverified", "exit_code": code,
                  "transcript": (health_log if health_log.exists() else setup_log).name}

    log = out / "claude-agent.jsonl"
    prompt = ("Use only the Blender MCP tools. Inspect the scene and capture the Case front camera "
              "at 35 percent using observe. Report the session ID and shape visible in the image.")
    code = await command(executable, (
        "--print", prompt, "--no-session-persistence", "--output-format", "stream-json", "--verbose",
        "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {"blender": mcp}}),
        "--tools", "", "--allowedTools", "mcp__blender__inspect_scene,mcp__blender__observe",
        "--permission-mode", "dontAsk",
    ), ROOT, env, log, timeout=60)
    return {"local_client": client, "agent_turn": model_evidence(log, code)}
