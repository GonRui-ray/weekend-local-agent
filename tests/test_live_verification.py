"""Operational acceptance must reject sample/map-only output and missing wiring.

Suppliers are controlled in these tests. They are not live-provider evidence.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "space"))
sys.path.insert(0, str(ROOT / "scripts"))
from app import build_app
from weekend_agent.server import create_pipeline
from weekend_agent.domain.understanding import demo_request
from verify_live_backend import verify_backend, inspect_plan, VerificationError, public_url
from connect_cloud import connect
from test_pipeline import live_handler


class LiveAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {"AMAP_KEY": "test", "TAVILY_KEY": "test", "MODEL_API_KEY": "",
                              "EVENT_FEED_URL": "", "EVENT_FEED_PATH": "", "EVENT_FEED_TOKEN": "",
                              "CORS_ORIGINS": "https://gonrui-ray.github.io"})
        self.env.start()
        self.suppliers = httpx.AsyncClient(transport=httpx.MockTransport(live_handler))
        self.pipeline, _ = create_pipeline(self.suppliers)
        self.app = build_app(pipeline=self.pipeline, mount_gradio=False)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="https://backend.example.test")
        self.request = demo_request()

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.suppliers.aclose()
        self.env.stop()

    async def verify(self, **kwargs):
        return await verify_backend("https://backend.example.test", [self.request], client=self.client, **kwargs)

    async def test_workflow_includes_live_mode_and_same_session_invitation(self):
        report = await self.verify()
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["cities"][0]["invitation_verified"])
        self.assertFalse(report["cities"][0]["all_plans_have_dated_records"])
        encoded = json.dumps(report)
        self.assertNotIn("conversation", encoded)
        self.assertNotIn("武林广场", encoded)

    async def test_search_snippets_do_not_pass_dated_activity_acceptance(self):
        report = await self.verify(require_dated=True)
        self.assertFalse(report["passed"])
        self.assertIn("日期", report["error"])

    async def test_dated_feed_can_pass_strict_acceptance_without_search(self):
        self.pipeline.activities.key = ""
        self.pipeline.activities.feed_url = "https://feed.example.test/events"
        records = [{"id": str(i), "city": "杭州", "title": "有日期的活动" + str(i),
                    "venue": "美术馆" + str(i), "category": "看展", "startsOn": self.request["date"],
                    "endsOn": self.request["date"], "url": "https://culture.example.test/" + str(i),
                    "kind": "official", "publisher": "受控来源"} for i in range(3)]
        def handler(request):
            if request.url.host == "feed.example.test":
                return httpx.Response(200, json={"events": records})
            if request.url.path.endswith("/place/text"):
                return httpx.Response(200, json={"status": "1", "pois": []})
            return live_handler(request)
        self.suppliers._transport = httpx.MockTransport(handler)
        report = await self.verify(require_dated=True)
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["cities"][0]["all_plans_have_dated_records"])

    async def test_map_only_routes_are_not_a_connected_activity_workflow(self):
        self.pipeline.activities.key = ""
        report = await self.verify()
        self.assertFalse(report["passed"])
        self.assertIn("只有地图地点", report["error"])

    async def test_readiness_failure_does_not_spend_supplier_requests(self):
        with patch.dict(os.environ, {"AMAP_KEY": ""}):
            with patch.object(self.pipeline.places, "find", side_effect=AssertionError("must not call")):
                report = await self.verify()
        self.assertFalse(report["passed"])
        self.assertIn("/ready", report["error"])

    async def test_cors_must_allow_existing_pages(self):
        with patch.dict(os.environ, {"CORS_ORIGINS": "https://other.github.io"}):
            app = build_app(pipeline=self.pipeline, mount_gradio=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app)) as client:
            report = await verify_backend("https://backend.example.test", [self.request], client=client)
        self.assertFalse(report["passed"])
        self.assertIn("CORS", report["error"])

    async def test_sample_cannot_be_misreported_as_live(self):
        response = await self.client.post("/turn", json={"action": "demo"})
        reply = json.loads([line[5:] for line in response.text.splitlines() if line.startswith("data:")][-1])["data"]
        with self.assertRaisesRegex(VerificationError, "样例"):
            inspect_plan(reply, self.request)

    async def test_overbudget_is_rejected_even_when_response_claims_live(self):
        response = await self.client.post("/turn", json={"request": self.request})
        reply = json.loads([line[5:] for line in response.text.splitlines() if line.startswith("data:")][-1])["data"]
        changed = copy.deepcopy(reply)
        changed["next_step"]["result"]["plans"][0]["budgetPerPerson"]["total"] = self.request["budget"] + 1
        with self.assertRaisesRegex(VerificationError, "超预算"):
            inspect_plan(changed, self.request)

    def test_only_https_service_origins_are_accepted(self):
        for url in ("http://localhost:7860", "https://host/health", "https://secret@host", "https://host?key=secret"):
            self.assertFalse(public_url(url), url)
        self.assertTrue(public_url("https://example.onrender.com/"))


class ConnectionGateTests(unittest.TestCase):
    def test_failed_live_acceptance_does_not_change_pages(self):
        report = {"passed": False, "error": "没有资讯来源"}
        with tempfile.TemporaryDirectory() as folder:
            with patch("connect_cloud.gh") as github, patch("connect_cloud.verify_backend", new=AsyncMock(return_value=report)):
                self.assertEqual(connect("https://backend.example.test", [], output=Path(folder) / "result.json"), 1)
        self.assertEqual(github.call_args_list, [unittest.mock.call("auth", "status", "--hostname", "github.com")])

    def test_check_only_never_writes_a_variable_or_dispatches_pages(self):
        report = {"passed": True, "cities": [{"all_plans_have_dated_records": False}]}
        with tempfile.TemporaryDirectory() as folder:
            with patch("connect_cloud.gh") as github, patch("connect_cloud.verify_backend", new=AsyncMock(return_value=report)):
                self.assertEqual(connect("https://backend.example.test", [], check_only=True,
                                         output=Path(folder) / "result.json"), 0)
        self.assertEqual(github.call_count, 1)


if __name__ == "__main__":
    unittest.main()
