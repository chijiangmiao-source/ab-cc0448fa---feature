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


class SeparationApiTest(unittest.TestCase):
    def setUp(self):
        registry.start_all()
        self.server = build_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        _wait_closed(self.server)
        self.thread.join(timeout=5)

    def _request(self, method, path, body):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _payload(self, rects, start=(0, 0), end=(5, 5)):
        return {
            "rectangles": rects,
            "start": {"x": start[0], "y": start[1]},
            "end": {"x": end[0], "y": end[1]},
        }

    def test_tangent_chain_global_min_cut(self):
        # 相切连通：三框仅以共边相接，中间框最便宜。
        body = self._payload(
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 50},
                {"id": "b", "x1": 2, "y1": 0, "x2": 4, "y2": 2, "cost": 1},
                {"id": "c", "x1": 4, "y1": 0, "x2": 6, "y2": 2, "cost": 50},
            ],
            start=(0, 0),
            end=(6, 0),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "separated")
        self.assertEqual(payload["removed"], ["b"])
        self.assertEqual(payload["total_cost"], "1")
        self.assertEqual(payload["reachable_from_start"], ["a"])

    def test_corner_touch_is_connectivity(self):
        # 仅角点相接：面积重叠为 0，但闭合交集非空必须连通。
        body = self._payload(
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1},
                {"id": "b", "x1": 1, "y1": 1, "x2": 2, "y2": 2, "cost": 9},
            ],
            start=(0, 0),
            end=(2, 2),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["removed"], ["a"])

    def test_endpoint_multiple_coverage_cuts_both_routes(self):
        body = self._payload(
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 1, "cost": 100},
                {"id": "b", "x1": 0, "y1": 1, "x2": 2, "y2": 2, "cost": 100},
                {"id": "c", "x1": 2, "y1": 0, "x2": 4, "y2": 1, "cost": 1},
                {"id": "d", "x1": 2, "y1": 1, "x2": 4, "y2": 2, "cost": 1},
            ],
            start=(0, 1),
            end=(4, 1),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["removed"], ["c", "d"])
        self.assertEqual(payload["total_cost"], "2")
        self.assertEqual(payload["reachable_from_start"], ["a", "b"])

    def test_equal_cost_lexicographic_verdict(self):
        body = self._payload(
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 5},
                {"id": "b", "x1": 1, "y1": 0, "x2": 2, "y2": 1, "cost": 5},
                {"id": "c", "x1": 2, "y1": 0, "x2": 3, "y2": 1, "cost": 5},
            ],
            start=(0, 0),
            end=(3, 0),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["removed"], ["a"])

    def test_geometric_duplicates_distinct_sources(self):
        body = self._payload(
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 3},
                {"id": "b", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 4},
            ],
            start=(0, 0),
            end=(2, 2),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["removed"], ["a", "b"])
        self.assertEqual(payload["total_cost"], "7")
        self.assertEqual(payload["reachable_from_start"], [])

    def test_not_covered_stable_no_partial_plan(self):
        body = self._payload(
            [{"id": "a", "x1": 1, "y1": 1, "x2": 2, "y2": 2, "cost": 1}],
            start=(0, 0),
            end=(1, 1),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "not_covered")
        self.assertEqual(payload["point"], "start")
        self.assertNotIn("removed", payload)
        self.assertNotIn("total_cost", payload)

    def test_already_separated_stable_conclusion(self):
        body = self._payload(
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1},
                {"id": "b", "x1": 5, "y1": 5, "x2": 6, "y2": 6, "cost": 1},
            ],
            start=(0, 0),
            end=(6, 6),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {"status": "already_separated", "reason": "points_not_connected"},
        )

    def test_invalid_cost_rejected_with_location(self):
        body = self._payload(
            [
                {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 0},
                {"id": "b", "x1": 2, "y1": 0, "x2": 4, "y2": 2, "cost": 1},
            ],
            start=(0, 0),
            end=(4, 0),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["location"], {"index": 0, "id": "a"})
        self.assertNotIn("removed", payload)

    def test_missing_point_400(self):
        body = {
            "rectangles": [
                {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1}
            ],
            "start": {"x": 0, "y": 0},
        }
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_too_many_rects_400(self):
        body = self._payload(
            [
                {"id": f"r{i}", "x1": i, "y1": 0, "x2": i + 1, "y2": 1, "cost": 1}
                for i in range(161)
            ],
            start=(0, 0),
            end=(161, 0),
        )
        status, payload = self._request("POST", "/api/separation-audit", body)
        self.assertEqual(status, 400)
        self.assertIn("1 and 160", payload["error"]["message"])

    def test_audit_endpoint_still_works_alongside(self):
        # 既有 /api/audit 语义保持不变。
        body = [
            {"id": "a", "x1": 0, "y1": 0, "x2": 3, "y2": 2},
            {"id": "b", "x1": 1, "y1": 1, "x2": 4, "y2": 3},
        ]
        status, payload = self._request("POST", "/api/audit", body)
        self.assertEqual((status, payload), (200, {"area": "10", "perimeter": "14"}))


if __name__ == "__main__":
    unittest.main()
