"""Persistent Chatterbox subprocess boundary with injectable process creation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading

from .tts_protocol import parse_worker_event, should_log_worker_stderr, worker_command


def chatterbox_launch(root, config, python, helper):
    root = Path(root)
    local_cache = root / ".cache"
    local_model = local_cache / "huggingface" / "hub" / "models--ResembleAI--chatterbox-turbo"
    snapshots = local_model / "snapshots"
    local_snapshot = next((
        path for path in sorted(snapshots.glob("*"), reverse=True)
        if (path / "t3_turbo_v1.safetensors").is_file()
        and (path / "s3gen_meanflow.safetensors").is_file()
    ), None)
    args = [
        str(python), "-u", str(helper),
        str(config.get("chatterbox_voice_reference", "")),
        str(config.get("tts_output_device", "")),
        str(config.get("tts_output_hostapi", "")),
        str(local_snapshot or ""),
    ]
    env = os.environ.copy()
    if config.get("portable_mode", False) or local_model.is_dir():
        env["HF_HOME"] = str(local_cache / "huggingface")
        env["XDG_CACHE_HOME"] = str(local_cache)
    return args, env


class ChatterboxProcess:
    def __init__(self, root, config, python, helper, log, popen=None):
        self.root = Path(root)
        self.config = config
        self.python = python
        self.helper = Path(helper)
        self.log = log
        self.popen = popen or subprocess.Popen
        self.proc = None
        self._stdin_lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        args, env = chatterbox_launch(
            self.root, self.config, self.python, self.helper
        )
        self.proc = self.popen(
            args, cwd=str(self.root), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
        message = self.read_event({"READY", "ERROR"})
        if message.get("event") != "READY":
            raise RuntimeError("Chatterbox worker startup error: " + str(message.get("detail", message)))
        return message

    def read_event(self, wanted):
        while self.running:
            line = self.proc.stdout.readline()
            if line == "":
                break
            message = parse_worker_event(line)
            if message is not None and message.get("event") in wanted:
                return message
            if str(line).strip():
                self.log("TTS_WORKER_OUTPUT", text=str(line).strip()[:300])
        raise RuntimeError("Chatterbox worker stopped before expected event.")

    def send(self, payload):
        if not self.running:
            raise RuntimeError("Chatterbox worker is not running.")
        with self._stdin_lock:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def shutdown(self):
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                self.send(worker_command("stop"))
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.proc = None

    def _drain_stderr(self, proc):
        try:
            for line in proc.stderr:
                line = line.strip()
                if should_log_worker_stderr(line):
                    self.log("TTS_WORKER_STDERR", text=line[:500])
        except Exception as exc:
            self.log("TTS_WORKER_STDERR_ERROR", detail=str(exc))
