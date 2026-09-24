"""端到端 HTTP 测试：真实起服务线程，走 socket 打请求。"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from app.components import registry
from app.server import AuditHandler, build_server


def _wait_closed(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


class ApiTest(unittest.TestCase):
    def setUp(self):
        registry.start_all()
        self.server = build_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        _wait_closed(self.server)
        self.thread.join(timeout=5)

    def _request(self, method: str, path: str, body=None, raw=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = None
        hdrs = headers or {}
        if raw is not None:
            data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs = {"Content-Type": "application/json", **hdrs}
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_acceptance_area10_perimeter14(self):
        status, payload = self._request(
            "POST",
            "/api/audit",
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 3, "y2": 2},
                {"id": "b", "x1": 1, "y1": 1, "x2": 4, "y2": 3},
            ],
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"area": "10", "perimeter": "14"})

    def test_adjacent_and_geometric_duplicate(self):
        status, payload = self._request(
            "POST",
            "/api/audit",
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 3},
                {"id": "b", "x1": 2, "y1": 0, "x2": 4, "y2": 3},
            ],
        )
        self.assertEqual((status, payload), (200, {"area": "12", "perimeter": "14"}))

        status, payload = self._request(
            "POST",
            "/api/audit",
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 3},
                {"id": "b", "x1": 0, "y1": 0, "x2": 2, "y2": 3},
            ],
        )
        self.assertEqual((status, payload), (200, {"area": "6", "perimeter": "10"}))

    def test_duplicate_id_rejected_with_location(self):
        status, payload = self._request(
            "POST",
            "/api/audit",
            [
                {"id": "x", "x1": 0, "y1": 0, "x2": 1, "y2": 1},
                {"id": "x", "x1": 2, "y1": 2, "x2": 3, "y2": 3},
            ],
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_rectangle")
        self.assertEqual(payload["error"]["location"], {"index": 1, "id": "x"})
        self.assertNotIn("area", payload)
        self.assertNotIn("perimeter", payload)

    def test_invalid_rectangle_400_no_partial_result(self):
        status, payload = self._request(
            "POST",
            "/api/audit",
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 4, "y2": 4},
                {"id": "b", "x1": 9, "y1": 9, "x2": 9, "y2": 9},
            ],
        )
        self.assertEqual(status, 400)
        self.assertNotIn("area", payload)
        self.assertEqual(payload["error"]["location"]["index"], 1)

    def test_invalid_json(self):
        status, payload = self._request("POST", "/api/audit", raw="{not json")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_wrapped_payload_supported(self):
        status, payload = self._request(
            "POST",
            "/api/audit",
            {"rectangles": [{"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]},
        )
        self.assertEqual((status, payload), (200, {"area": "1", "perimeter": "4"}))

    def test_health_ok_when_components_ready(self):
        status, payload = self._request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(all(c["ready"] for c in payload["components"].values()))

    def test_health_reflects_component_failure(self):
        # 模拟一个组件自检失败：健康检查必须 503。
        from app.components import Component

        saved = registry.scan_engine
        try:
            def boom():
                raise RuntimeError("simulated engine failure")

            registry.scan_engine = Component("scan-engine", boom)
            registry.scan_engine.start()
            status, payload = self._request("GET", "/healthz")
            self.assertEqual(status, 503)
            self.assertEqual(payload["status"], "not_ready")
            self.assertFalse(
                payload["components"]["scan-engine"]["ready"]
            )
        finally:
            registry.scan_engine = saved

    def test_audit_503_when_not_ready(self):
        from app.components import Component

        saved = registry.validator
        try:
            def boom():
                raise RuntimeError("simulated validator failure")

            registry.validator = Component("request-validator", boom)
            registry.validator.start()
            status, payload = self._request(
                "POST",
                "/api/audit",
                [{"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            )
            self.assertEqual(status, 503)
            self.assertEqual(payload["error"]["code"], "not_ready")
        finally:
            registry.validator = saved

    def test_unknown_route_404(self):
        status, _ = self._request("GET", "/nope")
        self.assertEqual(status, 404)

    # -- /api/separation-audit -------------------------------------------

    def test_separation_tangent_chain(self):
        status, payload = self._request(
            "POST",
            "/api/separation-audit",
            {
                "rectangles": [
                    {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 5},
                    {"id": "b", "x1": 1, "y1": 0, "x2": 2, "y2": 1, "cost": 2},
                    {"id": "c", "x1": 2, "y1": 0, "x2": 3, "y2": 1, "cost": 9},
                ],
                "start": {"x": 0, "y": 0},
                "end": {"x": 2, "y": 0},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {
                "status": "cleaned",
                "cleaned": ["b"],
                "total_cost": "2",
                "reachable_from_start": ["a"],
            },
        )

    def test_separation_endpoint_multi_coverage(self):
        status, payload = self._request(
            "POST",
            "/api/separation-audit",
            {
                "rectangles": [
                    {"id": "m1", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 4},
                    {"id": "m2", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 6},
                    {"id": "b", "x1": 2, "y1": 0, "x2": 3, "y2": 2, "cost": 1},
                ],
                "start": {"x": 1, "y": 1},
                "end": {"x": 2, "y": 0},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["cleaned"], ["m1", "m2"])
        self.assertEqual(payload["total_cost"], "10")
        self.assertEqual(payload["reachable_from_start"], [])

    def test_separation_same_cost_canonical_verdict(self):
        # {m1,m2} 与 {s} 同代价：清洗列表逐元素字典序取 ["m1","m2"]。
        status, payload = self._request(
            "POST",
            "/api/separation-audit",
            {
                "rectangles": [
                    {"id": "s", "x1": 0, "y1": 0, "x2": 1, "y2": 5, "cost": 10},
                    {"id": "m1", "x1": 1, "y1": 3, "x2": 4, "y2": 5, "cost": 5},
                    {"id": "m2", "x1": 1, "y1": 0, "x2": 4, "y2": 2, "cost": 5},
                    {"id": "t", "x1": 4, "y1": 0, "x2": 5, "y2": 5, "cost": 20},
                ],
                "start": {"x": 0, "y": 2},
                "end": {"x": 4, "y": 2},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["cleaned"], ["m1", "m2"])
        self.assertEqual(payload["total_cost"], "10")
        self.assertEqual(payload["reachable_from_start"], ["s"])

    def test_separation_already_separated_no_partial_plan(self):
        status, payload = self._request(
            "POST",
            "/api/separation-audit",
            {
                "rectangles": [
                    {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 5},
                    {"id": "b", "x1": 3, "y1": 0, "x2": 4, "y2": 1, "cost": 5},
                ],
                "start": {"x": 0, "y": 0},
                "end": {"x": 3, "y": 0},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {
                "status": "already_separated",
                "cleaned": [],
                "total_cost": "0",
                "reachable_from_start": ["a"],
            },
        )

    def test_separation_point_not_covered(self):
        body = {
            "rectangles": [
                {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1}
            ],
            "start": {"x": 9, "y": 9},
            "end": {"x": 0, "y": 0},
        }
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "point_not_covered", "point": "start"})
        self.assertNotIn("cleaned", payload)

        body["start"] = {"x": 0, "y": 0}
        body["end"] = {"x": 9, "y": 9}
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(payload, {"status": "point_not_covered", "point": "end"})

    def test_separation_validation_400(self):
        base = {
            "rectangles": [
                {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1}
            ],
            "start": {"x": 0, "y": 0},
            "end": {"x": 1, "y": 1},
        }
        # 缺 cost。
        bad = {**base, "rectangles": [
            {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]}
        status, payload = self._request("POST", "/api/separation-audit", bad)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_rectangle")
        self.assertEqual(payload["error"]["location"], {"index": 0, "id": "a"})
        self.assertNotIn("cleaned", payload)

        # 坏点坐标。
        bad = {**base, "end": {"x": 1.5, "y": 1}}
        status, payload = self._request("POST", "/api/separation-audit", bad)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_point")
        self.assertEqual(payload["error"]["location"], {"field": "end"})

        # 非对象体。
        status, payload = self._request("POST", "/api/separation-audit", [1])
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_payload")

        # 非法 JSON。
        status, payload = self._request(
            "POST", "/api/separation-audit", raw="{not json"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_separation_503_when_not_ready(self):
        from app.components import Component

        saved = registry.separation_engine
        try:
            def boom():
                raise RuntimeError("simulated separation engine failure")

            registry.separation_engine = Component("separation-engine", boom)
            registry.separation_engine.start()
            status, payload = self._request(
                "POST",
                "/api/separation-audit",
                {
                    "rectangles": [
                        {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1}
                    ],
                    "start": {"x": 0, "y": 0},
                    "end": {"x": 1, "y": 1},
                },
            )
            self.assertEqual(status, 503)
            self.assertEqual(payload["error"]["code"], "not_ready")
        finally:
            registry.separation_engine = saved


if __name__ == "__main__":
    unittest.main()
