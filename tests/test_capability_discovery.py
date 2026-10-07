"""Capability / tool discovery wiring tests.

These tests pin the wiring contract rather than the implementation:

* the production registry contains the real built-in tools and rejects
  duplicates;
* capability enumeration reuses the same adapters as ``resolve`` and is
  deterministic, read-only, and side-effect free;
* the discovery API reports registered capabilities and tools, not an echo of
  the request;
* nothing sensitive is exposed, and no host environment probing happens.

Host environment discovery is deliberately not implemented and is not covered
here beyond asserting that it does not leak into these endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.fabric.fabric import CapabilityFabric
from app.fabric.models import CapabilityDescriptor, CapabilityDomain
from app.tools.contracts import ToolInvocation, ToolStatus
from app.tools.registry import (
    DuplicateToolError,
    ToolRegistry,
    build_default_tool_registry,
)
from app.tools.read_project_file import ReadProjectFile
from app.tools.write_project_file import WriteProjectFile


class ProductionToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_default_registry_contains_real_production_tools(self) -> None:
        registry = build_default_tool_registry(self.root, allowed_files=("a.txt",))
        ids = [definition.id for definition in registry.list_tools()]
        self.assertEqual(ids, ["read_project_file", "write_project_file"])
        self.assertTrue(registry.contains(WriteProjectFile.TOOL_ID))
        self.assertTrue(registry.contains(ReadProjectFile.TOOL_ID))

    def test_empty_allow_list_registers_read_but_permits_nothing(self) -> None:
        """A known project root registers READ deny-by-default, not permissively."""
        registry = build_default_tool_registry(self.root, allowed_files=())
        self.assertTrue(registry.contains(ReadProjectFile.TOOL_ID))
        tool = registry.get(ReadProjectFile.TOOL_ID)
        result = tool.execute(
            ToolInvocation(
                ReadProjectFile.TOOL_ID, {"relative_path": "a.txt"}, "inv-1"
            )
        )
        self.assertNotEqual(
            result.status, ToolStatus.COMPLETED, "empty allow-list must not permit a read"
        )

    def test_registry_without_project_root_still_lists_write_tool(self) -> None:
        registry = build_default_tool_registry()
        self.assertEqual(
            [definition.id for definition in registry.list_tools()],
            ["write_project_file"],
        )

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        with self.assertRaises(DuplicateToolError):
            registry.register(WriteProjectFile())

    def test_listing_is_sorted_and_deterministic(self) -> None:
        first = build_default_tool_registry(self.root, allowed_files=("a.txt",))
        second = build_default_tool_registry(self.root, allowed_files=("a.txt",))
        self.assertEqual(
            [d.id for d in first.list_tools()], [d.id for d in second.list_tools()]
        )

    def test_empty_custom_registry_still_works(self) -> None:
        """Library and unit-test callers keep the plain empty registry."""
        registry = ToolRegistry()
        self.assertEqual(registry.list_tools(), [])
        self.assertFalse(registry.contains("write_project_file"))


class FabricCapabilityEnumerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.registry = build_default_tool_registry(self.root, allowed_files=("a.txt",))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fabric_enumerates_registered_tools(self) -> None:
        fabric = CapabilityFabric(tool_registry=self.registry)
        caps = fabric.list_tool_capabilities()
        self.assertEqual(
            [c.capability_id for c in caps],
            ["cap:tool:read_project_file", "cap:tool:write_project_file"],
        )
        for cap in caps:
            self.assertEqual(cap.domain, CapabilityDomain.TOOL)
            self.assertIn("tool_id", cap.metadata)

    def test_fabric_without_registries_enumerates_nothing(self) -> None:
        """No registry means no capabilities, never a guess."""
        self.assertEqual(CapabilityFabric().list_capabilities(), ())
        self.assertEqual(CapabilityFabric().list_tool_capabilities(), ())

    def test_enumeration_is_deterministic(self) -> None:
        fabric = CapabilityFabric(tool_registry=self.registry)
        self.assertEqual(
            [c.capability_id for c in fabric.list_capabilities()],
            [c.capability_id for c in fabric.list_capabilities()],
        )

    def test_enumeration_does_not_mutate_the_registry(self) -> None:
        fabric = CapabilityFabric(tool_registry=self.registry)
        before = [d.id for d in self.registry.list_tools()]
        fabric.list_capabilities()
        fabric.list_tool_capabilities()
        self.assertEqual([d.id for d in self.registry.list_tools()], before)

    def test_enumeration_reflects_registry_changes(self) -> None:
        """Discovery reads current state; it is not a frozen snapshot or cache."""
        fabric = CapabilityFabric(tool_registry=self.registry)
        self.assertNotIn(
            "cap:tool:extra_tool", [c.capability_id for c in fabric.list_capabilities()]
        )

        class _ExtraTool:
            TOOL_ID = "extra_tool"

            @property
            def definition(self):
                from app.tools.contracts import ToolDefinition

                return ToolDefinition(
                    id=self.TOOL_ID, name="ExtraTool", description="late addition"
                )

        self.registry.register(_ExtraTool())
        self.assertIn(
            "cap:tool:extra_tool", [c.capability_id for c in fabric.list_capabilities()]
        )

    def test_descriptor_serialises_without_authority(self) -> None:
        fabric = CapabilityFabric(tool_registry=self.registry)
        blob = json.dumps([c.to_dict() for c in fabric.list_capabilities()])
        for forbidden in (
            "api_key",
            "credential",
            "password",
            "secret",
            "run_scope",
            "approval",
            "allowed_tool_ids",
        ):
            self.assertNotIn(forbidden, blob)

    def test_capability_descriptor_to_dict_shape(self) -> None:
        descriptor = CapabilityDescriptor(
            capability_id="cap:tool:x",
            domain=CapabilityDomain.TOOL,
            name="X",
            description="d",
            metadata={"tool_id": "x"},
        )
        self.assertEqual(
            descriptor.to_dict(),
            {
                "capability_id": "cap:tool:x",
                "domain": "tool",
                "name": "X",
                "description": "d",
                "metadata": {"tool_id": "x"},
            },
        )


class DiscoveryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.registry = build_default_tool_registry(self.root, allowed_files=("a.txt",))
        self.fabric = CapabilityFabric(tool_registry=self.registry)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _service(self):
        from app.api.service import ForgeApiService

        service = ForgeApiService.__new__(ForgeApiService)
        service._tool_registry = self.registry
        service._fabric = self.fabric
        return service

    def test_list_capabilities_returns_registered_descriptors(self) -> None:
        caps = self._service().list_capabilities()
        ids = [c["capability_id"] for c in caps]
        self.assertIn("cap:tool:write_project_file", ids)
        self.assertIn("cap:tool:read_project_file", ids)

    def test_list_tools_returns_real_tools_not_an_echo(self) -> None:
        tools = self._service().list_tools()
        self.assertEqual(
            [t["id"] for t in tools], ["read_project_file", "write_project_file"]
        )
        for tool in tools:
            self.assertTrue(tool["name"])
            self.assertTrue(tool["description"])

    def test_empty_registry_yields_empty_listings(self) -> None:
        from app.api.service import ForgeApiService

        service = ForgeApiService.__new__(ForgeApiService)
        service._tool_registry = ToolRegistry()
        service._fabric = CapabilityFabric()
        self.assertEqual(service.list_tools(), [])
        self.assertEqual(service.list_capabilities(), [])

    def test_discovery_response_has_no_sensitive_keys(self) -> None:
        service = self._service()
        blob = json.dumps(
            {"capabilities": service.list_capabilities(), "tools": service.list_tools()}
        )
        for forbidden in (
            "api_key",
            "credential",
            "password",
            "secret",
            "token",
            "run_scope",
            "approval",
        ):
            self.assertNotIn(forbidden, blob)

    def test_endpoints_return_200(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except Exception:  # pragma: no cover - optional dependency
            self.skipTest("fastapi TestClient unavailable")
        from app.api.server import create_api_app

        client = TestClient(create_api_app(service=self._service()))
        caps = client.get("/api/capabilities")
        self.assertEqual(caps.status_code, 200)
        self.assertTrue(any(c["domain"] == "tool" for c in caps.json()))
        tools = client.get("/api/tools")
        self.assertEqual(tools.status_code, 200)
        self.assertEqual(
            [t["id"] for t in tools.json()],
            ["read_project_file", "write_project_file"],
        )
        # Repeated calls are stable: enumeration performs no mutation.
        self.assertEqual(caps.json(), client.get("/api/capabilities").json())

    def test_service_registry_is_what_the_fabric_enumerates(self) -> None:
        """One registry object backs both execution and discovery."""
        from app.api.service import ForgeApiService
        from app.tools.workspace import Workspace

        service = ForgeApiService.__new__(ForgeApiService)
        workspace = Workspace(self.root)
        shared = build_default_tool_registry(self.root, allowed_files=("a.txt",))
        service._workspace = workspace
        service._tool_registry = shared
        service._fabric = CapabilityFabric(
            workspace=workspace, tool_registry=service._tool_registry
        )

        self.assertIs(service.tool_registry, shared)
        capability_tool_ids = {
            cap["metadata"]["tool_id"]
            for cap in service.list_capabilities()
            if cap["domain"] == "tool"
        }
        self.assertEqual(
            capability_tool_ids,
            {definition.id for definition in shared.list_tools()},
        )


if __name__ == "__main__":
    unittest.main()
