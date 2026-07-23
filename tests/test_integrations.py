from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from oida.integrations import TARGETS, _install_opencode, _stage_marketplace, assets_root
from oida.lifecycle import ensure_gateway, server_url, stop_gateway
from oida.mcp_server import MCP, manifest_resource, oida_harness, oida_listening_identity
from oida.server import create_app


class IntegrationAssetTests(unittest.TestCase):
    def test_host_skills_are_in_sync(self) -> None:
        root = assets_root()
        paths = [
            root / "codex" / "plugins" / "oida" / "skills" / "oida-listening" / "SKILL.md",
            root / "claude" / "plugins" / "oida" / "skills" / "oida-listening" / "SKILL.md",
            root / "hermes" / "skills" / "oida-listening" / "SKILL.md",
            root / "openclaw" / "plugins" / "oida" / "skills" / "oida-listening" / "SKILL.md",
            root / "opencode" / "skills" / "oida-listening" / "SKILL.md",
        ]
        contents = [path.read_text(encoding="utf-8") for path in paths]
        self.assertTrue(all(content == contents[0] for content in contents[1:]))
        self.assertIn("oida_harness", contents[0])
        self.assertIn("oida_listening_identity", contents[0])
        self.assertIn("Never fabricate", contents[0])
        self.assertIn("oida_prepare_turn", contents[0])

    def test_all_local_host_integrations_are_exposed(self) -> None:
        self.assertEqual(TARGETS, ("hermes", "codex", "claude", "openclaw", "opencode"))

    def test_codex_and_claude_mcp_commands_ensure_daemon(self) -> None:
        root = assets_root()
        codex = json.loads((root / "codex" / "plugins" / "oida" / ".mcp.json").read_text(encoding="utf-8"))
        claude = json.loads((root / "claude" / "plugins" / "oida" / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["mcpServers"]["oida"]["args"], ["gateway", "--stdio", "--ensure-daemon"])
        self.assertEqual(claude["oida"]["args"], ["gateway", "--stdio", "--ensure-daemon"])
        self.assertEqual(codex["mcpServers"]["oida"]["env"]["OIDA_MOSS_PREWARM"], "0")
        self.assertEqual(claude["oida"]["env"]["OIDA_MOSS_PREWARM"], "0")

    def test_staged_marketplace_pins_the_active_python_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"OIDA_DATA_DIR": tmp},
            clear=False,
        ):
            marketplace = _stage_marketplace("codex")
            config = json.loads(
                (marketplace / "plugins" / "oida" / ".mcp.json").read_text(
                    encoding="utf-8"
                )
            )
        server = config["mcpServers"]["oida"]
        self.assertEqual(Path(server["command"]), Path(os.sys.executable))
        self.assertEqual(server["args"][:2], ["-m", "oida.cli"])
        self.assertEqual(server["args"][-3:], ["gateway", "--stdio", "--ensure-daemon"])
        self.assertEqual(server["env"]["OIDA_MOSS_PREWARM"], "0")

    def test_openclaw_marketplace_is_staged_with_pinned_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OIDA_DATA_DIR": tmp}, clear=False):
            marketplace = _stage_marketplace("openclaw")
            config = json.loads((marketplace / "plugins" / "oida" / ".mcp.json").read_text(encoding="utf-8"))
        server = config["oida"]
        self.assertEqual(Path(server["command"]), Path(os.sys.executable))
        self.assertEqual(server["args"][-3:], ["gateway", "--stdio", "--ensure-daemon"])

    def test_opencode_installer_preserves_config_and_adds_local_mcp_and_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"OPENCODE_CONFIG_DIR": tmp},
            clear=False,
        ):
            config_path = Path(tmp) / "opencode.json"
            config_path.write_text(json.dumps({"theme": "system", "mcp": {"existing": {"type": "remote"}}}), encoding="utf-8")
            with patch("oida.integrations.shutil.which", return_value="/usr/local/bin/opencode"), patch(
                "oida.integrations._run", return_value={"ok": True, "status": 0, "output": "oida connected", "command": []}
            ):
                result = _install_opencode()
            installed = json.loads(config_path.read_text(encoding="utf-8"))
            skill = Path(tmp) / "skills" / "oida-listening" / "SKILL.md"
            skill_exists = skill.exists()

        self.assertTrue(result["installed"])
        self.assertEqual(installed["theme"], "system")
        self.assertIn("existing", installed["mcp"])
        self.assertEqual(installed["mcp"]["oida"]["command"][0], os.sys.executable)
        self.assertTrue(skill_exists)

    def test_mcp_surface_is_compact_and_complete(self) -> None:
        tools = {tool.name for tool in asyncio.run(MCP.list_tools())}
        resources = {resource.uri for resource in asyncio.run(MCP.list_resources())}
        prompts = {prompt.name for prompt in asyncio.run(MCP.list_prompts())}
        self.assertEqual(
            tools,
            {
                "oida_capabilities",
                "oida_listening_identity",
                "oida_route",
                "oida_listen",
                "oida_harness",
                "oida_ask",
                "oida_prepare_turn",
                "oida_commit_turn",
                "oida_memory_search",
                "oida_memory_get",
                "oida_remember",
                "oida_forget",
                "oida_covenant",
                "oida_live",
            },
        )
        self.assertIn("oida://manifest", {str(uri) for uri in resources})
        self.assertIn("listen_with_oida", prompts)

    def test_oida_mounts_complete_akousmata_navigator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "OIDA_DATA_DIR": str(Path(tmp) / "oida"),
                "OIDA_AUDIO_DIR": str(Path(tmp) / "audio"),
                "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
            },
            clear=False,
        ):
            with TestClient(create_app(profile="stub"), base_url="http://127.0.0.1") as client:
                page = client.get("/library/")
                health = client.get("/library/api/health")

        self.assertEqual(page.status_code, 200)
        self.assertIn("listening navigator", page.text.lower())
        self.assertEqual(health.status_code, 200)
        from akousmata_app import AKOUSMATA_CONTRACT

        self.assertEqual(health.json()["contract"], AKOUSMATA_CONTRACT)


class MountedMCPConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_listening_identity_tool_reads_and_explicitly_sets_the_document(self) -> None:
        with patch(
            "oida.mcp_server.get_json",
            return_value={"filename": "LISTENING.md", "active": False},
        ) as get, patch(
            "oida.mcp_server.put_json",
            return_value={"filename": "LISTENING.md", "active": True},
        ) as put:
            read = await oida_listening_identity()
            saved = await oida_listening_identity(action="set", text="Listen like a guest.")

        self.assertFalse(read["active"])
        self.assertTrue(saved["active"])
        get.assert_called_once_with(server_url(), "/listening")
        put.assert_called_once_with(
            server_url(),
            "/listening",
            {"text": "Listen like a guest."},
        )

    async def test_listening_identity_status_does_not_disclose_text_or_local_path(self) -> None:
        with patch(
            "oida.mcp_server.get_json",
            return_value={
                "filename": "LISTENING.md",
                "active": True,
                "sha256": "a" * 64,
                "text": "private orientation",
                "path": "/private/LISTENING.md",
            },
        ):
            status = await oida_listening_identity(action="status")

        self.assertEqual(status["sha256"], "a" * 64)
        self.assertNotIn("text", status)
        self.assertNotIn("path", status)

    async def test_gateway_proxies_leave_the_asgi_event_loop(self) -> None:
        event_loop_thread = threading.get_ident()
        worker_threads: list[int] = []

        def fake_post_json(*_args: object, **_kwargs: object) -> dict[str, bool]:
            worker_threads.append(threading.get_ident())
            return {"ok": True}

        def fake_get_json(*_args: object, **_kwargs: object) -> dict[str, str]:
            worker_threads.append(threading.get_ident())
            return {"contract": "oida/gateway/v0.4"}

        with patch("oida.mcp_server.post_json", side_effect=fake_post_json), patch(
            "oida.mcp_server.get_json",
            side_effect=fake_get_json,
        ):
            result = await oida_harness(perception={})
            manifest = json.loads(await manifest_resource())

        self.assertEqual(result, {"ok": True})
        self.assertEqual(manifest["contract"], "oida/gateway/v0.4")
        self.assertEqual(len(worker_threads), 2)
        self.assertTrue(all(thread_id != event_loop_thread for thread_id in worker_threads))


class LifecycleTests(unittest.TestCase):
    def test_managed_stub_gateway_starts_once_and_stops(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "OIDA_DATA_DIR": str(Path(tmp) / "data"),
                "OIDA_AUDIO_DIR": str(Path(tmp) / "audio"),
                "OIDA_PORT": str(port),
                "OIDA_HOST": "127.0.0.1",
                "OIDA_MOSS_PREWARM": "0",
                "AKOUSMATA_PATH": str(Path(tmp) / "akousmata"),
                "AKOUSMATA_WATCHER": "0",
            },
            clear=False,
        ):
            first = ensure_gateway(profile="stub", timeout=15)
            second = ensure_gateway(profile="stub", timeout=5)
            with self.assertRaisesRegex(RuntimeError, "already running with profile"):
                ensure_gateway(profile="mac-mps", timeout=5)
            stopped = stop_gateway(timeout=10)

        self.assertTrue(first["running"])
        self.assertTrue(first.get("started"))
        self.assertTrue(second["running"])
        self.assertFalse(second.get("started", False))
        self.assertTrue(stopped["stopped"])


if __name__ == "__main__":
    unittest.main()
