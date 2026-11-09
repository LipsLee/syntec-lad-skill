from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER = PROJECT_ROOT / "scripts" / "syntec_lad_mcp.py"


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER)],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (
            self.process.stdin,
            self.process.stdout,
            self.process.stderr,
        ):
            if stream is not None:
                stream.close()

    def request(self, payload: dict) -> dict:
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        self.assertTrue(line, "MCP server returned no response")
        return json.loads(line)

    def test_initialize_and_tools_list(self) -> None:
        initialized = self.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            }
        )
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "syntec-lad")

        listed = self.request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("inspect_lad", names)
        self.assertIn("lookup_address", names)
        self.assertIn("read_reference", names)

    def test_lookup_and_reference_tools(self) -> None:
        found = self.request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "lookup_address",
                    "arguments": {"query": "C6"},
                },
            }
        )
        self.assertFalse(found["result"]["isError"])
        self.assertIn("C6", found["result"]["content"][0]["text"])

        reference = self.request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "read_reference",
                    "arguments": {"name": "panming/address-map.md"},
                },
            }
        )
        self.assertFalse(reference["result"]["isError"])
        self.assertIn("地址表", reference["result"]["content"][0]["text"])

    def test_reference_path_escape_is_rejected(self) -> None:
        escaped = self.request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "read_reference",
                    "arguments": {"name": "../SKILL.md"},
                },
            }
        )
        self.assertTrue(escaped["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
