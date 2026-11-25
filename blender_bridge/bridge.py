"""Authenticated loopback transport. Only pump() calls into Blender."""
import hashlib
import hmac
import json
import logging
import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_REQUEST = 256 * 1024
MAX_JOBS = 48
MAX_RESULTS = 24 * 1024 * 1024
log = logging.getLogger(__name__)


@dataclass
class Job:
    action: str
    args: dict
    fingerprint: bytes
    submitted: float
    state: str = "queued"
    result: dict | None = None
    size: int = 0
    complete: threading.Event = field(default_factory=threading.Event)

    def status(self):
        return {"state": self.state, "action": self.action}


class Bridge:
    def __init__(self, dispatch, token, port):
        if len(token) < 16:
            raise ValueError("BLENDER_MCP_TOKEN must contain at least 16 characters")
        self.dispatch = dispatch
        self.jobs = OrderedDict()
        self.expired = OrderedDict()
        self.pending = queue.SimpleQueue()
        self.lock = threading.Lock()

        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, format, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def do_POST(self):
                try:
                    code, result = self.handle_request()
                    body = json.dumps(result, allow_nan=False).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def handle_request(self):
                auth = self.headers.get("Authorization", "").encode()
                if not hmac.compare_digest(auth, ("Bearer " + token).encode()):
                    return 401, {"error": "Bridge token rejected"}
                if self.path not in ("/job", "/frames"):
                    return 404, {"error": "Unknown route"}
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= MAX_REQUEST:
                        raise ValueError("Invalid body size")
                    request = json.loads(self.rfile.read(length))
                    if self.path == "/frames":
                        return 200, bridge.frames(request)
                    return 200, bridge.request(request)
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    return 400, {"error": str(exc)}

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def frames(self, request):
        """Snapshot retained camera results without entering Blender's queue."""
        cameras = request["cameras"]
        if (not isinstance(cameras, list) or not 1 <= len(cameras) <= 6
                or not all(isinstance(name, str) for name in cameras)):
            raise ValueError("Request one to six camera names")
        pair = {}
        with self.lock:
            for side in ("before", "after"):
                job_id = request[f"{side}_job_id"]
                job = self.jobs.get(job_id)
                result = job.result if job and job.state == "succeeded" else None
                if result is None:
                    raise ValueError(f"The {side} job {job_id!r} has no retained completed result")
                if result.get("source") != "camera_render":
                    raise ValueError(f"The {side} job must contain camera renders")
                images = {image["label"]: image for image in result["images"]}
                missing = set(cameras) - images.keys()
                if missing:
                    raise ValueError(f"The {side} job lacks these camera renders: {', '.join(sorted(missing))}")
                pair[side] = {key: result[key] for key in ("session_id", "scene", "frame", "revision")}
                pair[side].update(job_id=job_id, images=[images[camera] for camera in cameras])
        return pair

    def request(self, request):
        job_id = request["job_id"]
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 100:
            raise ValueError("Invalid job ID")
        with self.lock:
            job = self.jobs.get(job_id)
            if request.get("status_only") or request.get("cancel"):
                if request.get("cancel") and job and job.state == "queued":
                    job.state = "cancelled"
                    job.complete.set()
                known = job.status() if job else self.expired.get(job_id)
                return {"job_id": job_id, **(known or {"state": "unknown"})}
            if job_id in self.expired:
                return {"job_id": job_id, **self.expired[job_id]}
            action, args = request["action"], request["args"]
            canonical = json.dumps([action, args], sort_keys=True, allow_nan=False)
            fingerprint = hashlib.sha256(canonical.encode()).digest()
            if job:
                if job.fingerprint != fingerprint:
                    raise ValueError("Job ID is already bound to different arguments")
            else:
                self.trim()
                if len(self.jobs) >= MAX_JOBS:
                    raise ValueError("Job queue is full")
                job = self.jobs[job_id] = Job(action, args, fingerprint, time.perf_counter())
                self.pending.put(job_id)
        if request.get("await_result"):
            job.complete.wait(1)
        with self.lock:
            result = job.result
            expired_result = job.state in {"succeeded", "failed"} and result is None
            return {"job_id": job_id, **job.status(), "result_expired": expired_result, **(result or {})}

    def trim(self):
        total = sum(job.size for job in self.jobs.values())
        for job in self.jobs.values():
            if total <= MAX_RESULTS:
                break
            if job.result is not None:
                job.result = None
                total -= job.size
                job.size = 0
        if len(self.jobs) >= MAX_JOBS:
            expired = next((key for key, job in self.jobs.items()
                            if job.state not in {"queued", "running"}), None)
            if expired:
                job = self.jobs.pop(expired)
                self.expired[expired] = {**job.status(), "result_expired": True}
                if len(self.expired) > 4096:
                    self.expired.popitem(last=False)

    def pump(self):
        try:
            job_id = self.pending.get_nowait()
        except queue.Empty:
            return 0.02
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.state != "queued":
                return 0.02
            job.state = "running"
        started = time.perf_counter()
        try:
            result = self.dispatch(job.action, job.args)
            state = "succeeded"
        except (ValueError, RuntimeError, OSError) as exc:
            result = {"error": str(exc)}
            state = "failed"
        except Exception:
            log.exception("Blender MCP operation failed")
            result = {"error": "Unexpected Blender error. See Blender's console."}
            state = "failed"
        result.setdefault("timing_ms", {}).update({
            "queue": round((started - job.submitted) * 1000, 2),
            "blender_total": round((time.perf_counter() - started) * 1000, 2),
        })
        with self.lock:
            job.state = state
            job.result = result
            job.size = sum(len(img["data"]) for img in result.get("images", ()))
            job.complete.set()
        return 0.02

    def close(self):
        self.server.shutdown()
        self.server.server_close()
