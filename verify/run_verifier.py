"""一次性验证服务。

在 Compose 中运行一次并退出：
1. 代码测试（unittest 全量）；
2. 构建检查（compileall）；
3. 等待审计服务健康（请求校验器与扫描引擎均就绪）；
4. API/HTTP 冒烟：
   - 重叠框：面积 10、周长 14；
   - 相邻方框：不计公共边（面积 12、周长 14）；
   - 几何重复框：不增量（面积 6、周长 10）；
   - 标识重复 / 非法矩形：400 且带输入位置、不夹带部分结果；
   - 隔离裁决：相切连通链、角点相接连通、端点多重覆盖、同代价字典序裁决、
     几何重复分别清除、未覆盖/原本不连通的稳定结论。

全部通过退出码 0，否则 1。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8080")
WORKDIR = os.environ.get("AUDIT_WORKDIR", "/srv")
HEALTH_TIMEOUT_SECONDS = float(os.environ.get("VERIFY_HEALTH_TIMEOUT", "30"))


def _ok(label: str, detail: str = "") -> None:
    print(f"[PASS] {label}" + (f" -- {detail}" if detail else ""), flush=True)


def _fail(label: str, detail: str) -> None:
    print(f"[FAIL] {label} -- {detail}", flush=True)


def run_code_tests() -> bool:
    print("== 1/5 代码测试 ==", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=WORKDIR,
    )
    if proc.returncode == 0:
        _ok("代码测试全部通过")
    else:
        _fail("代码测试", f"exit={proc.returncode}")
    return proc.returncode == 0


def run_build_check() -> bool:
    print("== 2/5 构建检查（compileall）==", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "app", "verify", "tests"],
        cwd=WORKDIR,
    )
    if proc.returncode == 0:
        _ok("全部源码编译通过")
    else:
        _fail("构建检查", f"exit={proc.returncode}")
    return proc.returncode == 0


def wait_healthy() -> bool:
    print("== 3/5 等待服务健康（校验器 + 扫描引擎 + 隔离裁决器均就绪）==", flush=True)
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                if resp.status == 200 and payload.get("status") == "ok":
                    _ok("服务健康", json.dumps(payload["components"]))
                    return True
                last = f"status={resp.status} body={payload}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last = repr(exc)
        time.sleep(0.5)
    _fail("等待健康超时", last)
    return False


def _post(records):
    data = json.dumps(records).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/audit",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post_sep(rects, start, end):
    data = json.dumps(
        {"rectangles": rects, "start": {"x": start[0], "y": start[1]},
         "end": {"x": end[0], "y": end[1]}}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/separation-audit",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _check(label, cond, detail):
    if cond:
        _ok(label, detail)
        return True
    _fail(label, detail)
    return False


def run_http_smoke() -> bool:
    print("== 4/5 API/HTTP 冒烟（面积/周长）==", flush=True)
    ok = True

    # 验收构型：重叠框面积 10、周长 14。
    status, payload = _post(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 3, "y2": 2},
            {"id": "b", "x1": 1, "y1": 1, "x2": 4, "y2": 3},
        ]
    )
    want = {"area": "10", "perimeter": "14"}
    if status == 200 and payload == want:
        _ok("重叠框", f"{payload} == {want}")
    else:
        _fail("重叠框 面积10/周长14", f"status={status} payload={payload}")
        ok = False

    # 相邻方框：公共边不计（两框各周长 10，共边 3 抹掉两边 -> 14）。
    status, payload = _post(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 3},
            {"id": "b", "x1": 2, "y1": 0, "x2": 4, "y2": 3},
        ]
    )
    want = {"area": "12", "perimeter": "14"}
    if status == 200 and payload == want:
        _ok("相邻方框不计公共边", f"{payload} == {want}")
    else:
        _fail("相邻方框", f"status={status} payload={payload}")
        ok = False

    # 几何重复框：不增量。
    status, payload = _post(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 3},
            {"id": "b", "x1": 0, "y1": 0, "x2": 2, "y2": 3},
        ]
    )
    want = {"area": "6", "perimeter": "10"}
    if status == 200 and payload == want:
        _ok("重复框不增量", f"{payload} == {want}")
    else:
        _fail("重复框", f"status={status} payload={payload}")
        ok = False

    # 标识重复：400、带位置、无部分结果。
    status, payload = _post(
        [
            {"id": "x", "x1": 0, "y1": 0, "x2": 1, "y2": 1},
            {"id": "x", "x1": 2, "y1": 2, "x2": 3, "y2": 3},
        ]
    )
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    if (
        status == 400
        and err.get("location") == {"index": 1, "id": "x"}
        and "area" not in payload
    ):
        _ok("重复标识被拒绝且无部分结果", err.get("message", ""))
    else:
        _fail("重复标识", f"status={status} payload={payload}")
        ok = False

    # 非法矩形：400 且位置指向具体下标。
    status, payload = _post(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1},
            {"id": "b", "x1": 5, "y1": 0, "x2": 1, "y2": 1},
        ]
    )
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    if status == 400 and err.get("location", {}).get("index") == 1:
        _ok("非法矩形带输入位置", err.get("message", ""))
    else:
        _fail("非法矩形", f"status={status} payload={payload}")
        ok = False

    return ok


def run_separation_smoke() -> bool:
    print("== 5/5 隔离裁决冒烟（全局最小代价顶点割）==", flush=True)
    ok = True

    # 相切连通：三框仅共边相接，中间框最便宜，必须精确选中中间框。
    status, payload = _post_sep(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 50},
            {"id": "b", "x1": 2, "y1": 0, "x2": 4, "y2": 2, "cost": 1},
            {"id": "c", "x1": 4, "y1": 0, "x2": 6, "y2": 2, "cost": 50},
        ],
        (0, 0),
        (6, 0),
    )
    ok &= _check(
        "相切连通链全局最小割",
        status == 200
        and payload.get("removed") == ["b"]
        and payload.get("total_cost") == "1"
        and payload.get("reachable_from_start") == ["a"],
        f"status={status} payload={payload}",
    )

    # 仅角点相接：面积重叠为 0，按闭合交集必须连通。
    status, payload = _post_sep(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1},
            {"id": "b", "x1": 1, "y1": 1, "x2": 2, "y2": 2, "cost": 9},
        ],
        (0, 0),
        (2, 2),
    )
    ok &= _check(
        "角点相接按闭合交集连通",
        status == 200 and payload.get("removed") == ["a"],
        f"status={status} payload={payload}",
    )

    # 端点多重覆盖：起点/终点各被两个框覆盖，必须同时切断两端的便宜一侧。
    status, payload = _post_sep(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 1, "cost": 100},
            {"id": "b", "x1": 0, "y1": 1, "x2": 2, "y2": 2, "cost": 100},
            {"id": "c", "x1": 2, "y1": 0, "x2": 4, "y2": 1, "cost": 1},
            {"id": "d", "x1": 2, "y1": 1, "x2": 4, "y2": 2, "cost": 1},
        ],
        (0, 1),
        (4, 1),
    )
    ok &= _check(
        "端点多重覆盖两路同切",
        status == 200
        and payload.get("removed") == ["c", "d"]
        and payload.get("total_cost") == "2"
        and payload.get("reachable_from_start") == ["a", "b"],
        f"status={status} payload={payload}",
    )

    # 同代价规范裁决：切任一单框同价，取升序标识字典序最小。
    status, payload = _post_sep(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 5},
            {"id": "b", "x1": 1, "y1": 0, "x2": 2, "y2": 1, "cost": 5},
            {"id": "c", "x1": 2, "y1": 0, "x2": 3, "y2": 1, "cost": 5},
        ],
        (0, 0),
        (3, 0),
    )
    ok &= _check(
        "同代价取字典序最小方案",
        status == 200 and payload.get("removed") == ["a"],
        f"status={status} payload={payload}",
    )

    # 几何重复：两个完全重合的框互为独立来源，必须分别清除。
    status, payload = _post_sep(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 3},
            {"id": "b", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 4},
        ],
        (0, 0),
        (2, 2),
    )
    ok &= _check(
        "几何重复框分别清除",
        status == 200
        and payload.get("removed") == ["a", "b"]
        and payload.get("total_cost") == "7"
        and payload.get("reachable_from_start") == [],
        f"status={status} payload={payload}",
    )

    # 起点未覆盖：稳定结论，无部分方案。
    status, payload = _post_sep(
        [{"id": "a", "x1": 1, "y1": 1, "x2": 2, "y2": 2, "cost": 1}],
        (0, 0),
        (1, 1),
    )
    ok &= _check(
        "起点未覆盖给稳定结论且无部分方案",
        status == 200
        and payload.get("status") == "not_covered"
        and payload.get("point") == "start"
        and "removed" not in payload,
        f"status={status} payload={payload}",
    )

    # 两点原本不连通：稳定结论，无部分方案。
    status, payload = _post_sep(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1},
            {"id": "b", "x1": 5, "y1": 5, "x2": 6, "y2": 6, "cost": 1},
        ],
        (0, 0),
        (6, 6),
    )
    ok &= _check(
        "原本不连通给稳定结论且无部分方案",
        status == 200 and payload.get("status") == "already_separated",
        f"status={status} payload={payload}",
    )

    # 非法 cost：400 带位置、无部分结果。
    status, payload = _post_sep(
        [
            {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 0},
            {"id": "b", "x1": 2, "y1": 0, "x2": 4, "y2": 2, "cost": 1},
        ],
        (0, 0),
        (4, 0),
    )
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    ok &= _check(
        "非正清洗代价被拒绝且无部分结果",
        status == 400 and err.get("location") == {"index": 0, "id": "a"},
        f"status={status} payload={payload}",
    )

    return ok


def main() -> int:
    print(f"photomask-audit verifier -> {BASE_URL}", flush=True)
    results = [
        run_code_tests(),
        run_build_check(),
        wait_healthy(),
    ]
    if results[2]:
        results.append(run_http_smoke())
        results.append(run_separation_smoke())
    else:
        results.append(False)
        results.append(False)

    print("==================================", flush=True)
    if all(results):
        print("VERIFY RESULT: PASS (exit 0)", flush=True)
        return 0
    print("VERIFY RESULT: FAIL (exit 1)", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
