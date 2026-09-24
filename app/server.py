"""零第三方依赖的审计 HTTP 服务。

路由
----
* ``POST /api/audit`` —— 请求体为矩形数组（或 ``{"rectangles": [...]}``），
  成功返回 ``{"area": "十进制", "perimeter": "十进制"}``；
  校验失败返回 400 与带输入位置的稳定错误，且不夹带任何部分结果。
* ``POST /api/separation-audit`` —— 在合法矩形载荷上附加两个整数取样点
  （``start``/``end``）与每框正整数 ``cost``（≤160 框），全局最小代价
  顶点割裁决，返回清洗框、总代价与清除后起点可达分区。
* ``GET  /healthz``   —— 请求校验器、扫描引擎、隔离裁决器均就绪时 200，
  否则 503。
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .components import registry
from .geometry import GeometryError, audit_raw
from .separation import separation_raw

MAX_BODY_BYTES = 64 * 1024 * 1024


class AuditHandler(BaseHTTPRequestHandler):
    server_version = "PhotomaskAudit/1.0"

    # -- 工具 ------------------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # 安静一点
        if os.environ.get("AUDIT_HTTP_LOG"):
            super().log_message(fmt, *args)

    # -- 路由 ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib 命名)
        if self.path.split("?", 1)[0] == "/healthz":
            ready, components = registry.status()
            self._send_json(
                200 if ready else 503,
                {"status": "ok" if ready else "not_ready", "components": components},
            )
            return
        if self.path.split("?", 1)[0] == "/":
            self._send_json(
                200,
                {
                    "service": "photomask-defect-audit",
                    "endpoints": {
                        "audit": "POST /api/audit",
                        "separation_audit": "POST /api/separation-audit",
                        "health": "GET /healthz",
                    },
                },
            )
            return
        self._send_json(404, {"error": {"code": "not_found", "message": self.path}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in ("/api/audit", "/api/separation-audit"):
            self._send_json(404, {"error": {"code": "not_found", "message": self.path}})
            return

        ready, components = registry.status()
        if not ready:
            self._send_json(
                503,
                {
                    "error": {
                        "code": "not_ready",
                        "message": "service components not ready",
                        "components": components,
                    }
                },
            )
            return

        payload = self._read_json_body()
        if payload is None:
            return  # 错误响应已发出

        if path == "/api/separation-audit":
            try:
                result = separation_raw(payload)
            except GeometryError as exc:
                self._bad_request(
                    str(exc), exc.location, getattr(exc, "code", "invalid_request")
                )
                return
            self._send_json(200, result)
            return

        if isinstance(payload, dict) and "rectangles" in payload:
            records = payload["rectangles"]
        else:
            records = payload

        try:
            area, perimeter = audit_raw(records)
        except GeometryError as exc:
            # 全量校验先于计算：这里绝不会产生部分结果。
            self._bad_request(str(exc), exc.location, "invalid_rectangle")
            return

        self._send_json(200, {"area": area, "perimeter": perimeter})

    def _read_json_body(self):
        """读取并解析 JSON 请求体；失败时已发出 400，返回 None。"""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._bad_request("invalid Content-Length header", None, "bad_request")
            return None
        if length <= 0:
            self._bad_request("empty request body", None, "bad_request")
            return None
        if length > MAX_BODY_BYTES:
            self._bad_request(
                f"request body too large ({length} > {MAX_BODY_BYTES} bytes)",
                None,
                "payload_too_large",
            )
            return None

        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._bad_request(f"request body is not valid JSON: {exc}", None, "invalid_json")
            return None

    def _bad_request(
        self, message: str, location: dict | None, code: str = "invalid_rectangle"
    ) -> None:
        err: dict = {"code": code, "message": message}
        if location is not None:
            err["location"] = location
        self._send_json(400, {"error": err})


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), AuditHandler)


def main() -> None:
    host = os.environ.get("AUDIT_HOST", "0.0.0.0")
    port = int(os.environ.get("AUDIT_PORT", "8080"))
    # 先完成两个组件的启动自检，再开始接流量；健康检查与自检状态联动。
    registry.start_all()
    ready, components = registry.status()
    if not ready:
        raise SystemExit(f"components failed selftest: {components}")
    server = build_server(host, port)
    print(f"audit service listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
