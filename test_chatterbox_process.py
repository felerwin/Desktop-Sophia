import io
import tempfile
import unittest
from pathlib import Path

from ember import ChatterboxProcess, chatterbox_launch


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO('{"event":"READY","voice":"test"}\n')
        self.stderr = io.StringIO("")
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = -1

    def kill(self):
        self.returncode = -9


class ChatterboxProcessTests(unittest.TestCase):
    def test_launch_is_portable_and_finds_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = root / ".cache/huggingface/hub/models--ResembleAI--chatterbox-turbo/snapshots/abc"
            snapshot.mkdir(parents=True)
            (snapshot / "t3_turbo_v1.safetensors").touch()
            (snapshot / "s3gen_meanflow.safetensors").touch()
            args, env = chatterbox_launch(root, {"portable_mode": True}, "python", "worker.py")
        self.assertEqual(args[-2], str(snapshot))
        self.assertEqual(args[-1], "false")
        self.assertIn("HF_HOME", env)
        self.assertIn("XDG_CACHE_HOME", env)

    def test_fake_process_start_send_and_shutdown(self):
        fake = FakeProcess()
        calls = []
        process = ChatterboxProcess(
            ".", {}, "python", "worker.py",
            lambda event, **fields: calls.append((event, fields)),
            popen=lambda *args, **kwargs: fake,
        )
        self.assertEqual(process.start()["event"], "READY")
        process.send({"text": "hello"})
        self.assertIn('"text": "hello"', fake.stdin.getvalue())
        process.shutdown()
        self.assertFalse(process.running)
        self.assertIsNone(process.proc)


if __name__ == "__main__":
    unittest.main()
