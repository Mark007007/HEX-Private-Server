import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from integration.ai_bridge.client import AiBridgeClient, ProcessConfig


WORKER = textwrap.dedent(
    r"""
    import json
    import sys

    for line in sys.stdin:
        req = json.loads(line)
        print(json.dumps({
            "protocol": 1,
            "request_id": req["request_id"],
            "ok": True,
            "action": req["action"],
            "payload": {"n": req["payload"].get("n", 0)}
        }), flush=True)
    """
).strip()


class PersistentWorkerTests(unittest.TestCase):
    def test_same_process_handles_multiple_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "worker.py"
            script.write_text(WORKER, encoding="utf-8")
            client = AiBridgeClient(ProcessConfig(
                command=[sys.executable, str(script)],
                timeout_seconds=3,
            ))
            try:
                first_generation = client.generation
                self.assertEqual(client.call(
                    "r1", "decide", {"n": 1})["n"], 1)
                generation_after_first = client.generation
                self.assertGreater(generation_after_first, first_generation)

                self.assertEqual(client.call(
                    "r2", "decide", {"n": 2})["n"], 2)
                self.assertEqual(client.generation, generation_after_first)
            finally:
                client.close()

    def test_restart_creates_new_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "worker.py"
            script.write_text(WORKER, encoding="utf-8")
            client = AiBridgeClient(ProcessConfig(
                command=[sys.executable, str(script)],
                timeout_seconds=3,
            ))
            try:
                client.call("r1", "health", {})
                generation = client.generation
                client.restart()
                self.assertGreater(client.generation, generation)
                client.call("r2", "health", {})
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
