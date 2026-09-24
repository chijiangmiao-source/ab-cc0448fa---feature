"""污染带隔离审计：最小代价清洗集合的全局裁决。

给定最多 160 个带正整数清洗代价的闭合矩形与两个整数取样点，把每个矩形
看作图顶点：两矩形**闭交集非空**（含共边、共角点）即连边。取样点被矩形
覆盖（点在闭矩形内）时，该点挂在所有覆盖它的矩形上。要"清除"一组矩形
（付出其代价），使两点在剩余污染区域上不再连通。

裁决方式（全局精确，非逐次删除/局部贪心/面积重叠近似）
------------------------------------------------------
带权 s-t 顶点割经节点拆分化为有向最小割：每个矩形 v 拆成 ``v_in -> v_out``
（容量 = 清洗代价），无向边 (u, v) 换成两条无穷容量的有向边
``u_out -> v_in`` 与 ``v_out -> u_in``；超级源点以无穷容量连向所有覆盖
起点的矩形入点，所有覆盖终点的矩形出点以无穷容量连向超级汇点。一次
Dinic 最大流即得全局最小总代价。

同代价规范裁决
--------------
在所有最小代价割中取"升序框标识列表字典序最小"的方案。给每个矩形 v
（按标识升序排位 rank）配权 ``p_v = 2**(n-1-rank)``：主容量微扰为
``cost * B``（``B = 2**n`` 大于全部权之和，总代价严格主导）；再补
``source -> v_in`` 与 ``v_out -> sink`` 两条容量为 ``p_v`` 的边——
它们恰在 v **未被清洗**时（整点落在割的同一侧）计费 ``p_v``。于是
"最早分歧的标识是否出现在清洗列表中"成为最高优先的 0/1 位（出现严格
占优），一次最大流同时完成两级裁决，结果唯一。

清除后从起点仍可达的框标识在剩余图上用 BFS 复核，作为隔离证据随结果返回。
"""

from __future__ import annotations

from collections import deque

from .geometry import (
    COORD_BOUND,
    GeometryError,
    Rect,
    parse_rectangle_records,
)

MAX_RECTANGLES = 160

STATUS_CLEANED = "cleaned"
STATUS_ALREADY_SEPARATED = "already_separated"
STATUS_POINT_NOT_COVERED = "point_not_covered"


def _intersect(a: Rect, b: Rect) -> bool:
    """闭合矩形的非空交集：共边、共角点都算连通。"""
    return (
        a.x1 <= b.x2 and b.x1 <= a.x2
        and a.y1 <= b.y2 and b.y1 <= a.y2
    )


def _covers(rect: Rect, point: tuple[int, int]) -> bool:
    """点（含边界）是否被闭合矩形覆盖。"""
    px, py = point
    return rect.x1 <= px <= rect.x2 and rect.y1 <= py <= rect.y2


def _parse_point(payload: dict, field: str) -> tuple[int, int]:
    """解析 ``{"x": int, "y": int}`` 取样点，非法时抛带位置的稳定错误。"""
    loc = {"field": field}
    pt = payload.get(field)
    if not isinstance(pt, dict):
        raise GeometryError(
            f"'{field}' must be an object with integer 'x' and 'y'",
            loc,
            code="invalid_point",
        )
    coords = []
    for key in ("x", "y"):
        val = pt.get(key)
        if isinstance(val, bool) or not isinstance(val, int):
            raise GeometryError(
                f"'{field}.{key}' must be an integer", loc, code="invalid_point"
            )
        if abs(val) > COORD_BOUND:
            raise GeometryError(
                f"'{field}.{key}'={val} out of range [-1e9, 1e9]",
                loc,
                code="invalid_point",
            )
        coords.append(val)
    return coords[0], coords[1]


def parse_separation_payload(
    payload: dict,
) -> tuple[list[Rect], list[int], tuple[int, int], tuple[int, int]]:
    """全量校验隔离审计载荷；任何一项非法都抛 ``GeometryError``。"""
    if not isinstance(payload, dict):
        raise GeometryError(
            "request body must be an object with 'rectangles', 'start' and 'end'",
            code="invalid_payload",
        )
    if "rectangles" not in payload:
        raise GeometryError(
            "'rectangles' is required and must be a JSON array of rectangles",
            code="invalid_payload",
        )
    rects, costs = parse_rectangle_records(
        payload["rectangles"], max_count=MAX_RECTANGLES, require_cost=True
    )
    start = _parse_point(payload, "start")
    end = _parse_point(payload, "end")
    return rects, costs, start, end


class _Dinic:
    """整数容量 Dinic 最大流（容量可为任意大 Python 整数）。"""

    __slots__ = ("n", "to", "cap", "nxt", "head", "level", "it")

    def __init__(self, n: int) -> None:
        self.n = n
        self.to: list[int] = []
        self.cap: list[int] = []
        self.nxt: list[int] = []
        self.head: list[int] = [-1] * n
        self.level: list[int] = [0] * n
        self.it: list[int] = [0] * n

    def add_edge(self, u: int, v: int, c: int) -> None:
        self.to.append(v)
        self.cap.append(c)
        self.nxt.append(self.head[u])
        self.head[u] = len(self.to) - 1
        self.to.append(u)
        self.cap.append(0)
        self.nxt.append(self.head[v])
        self.head[v] = len(self.to) - 1

    def _bfs(self, s: int, t: int) -> bool:
        level = self.level
        for i in range(self.n):
            level[i] = -1
        level[s] = 0
        queue = deque([s])
        while queue:
            u = queue.popleft()
            e = self.head[u]
            while e != -1:
                if self.cap[e] > 0 and level[self.to[e]] < 0:
                    level[self.to[e]] = level[u] + 1
                    queue.append(self.to[e])
                e = self.nxt[e]
        return level[t] >= 0

    def _dfs(self, u: int, t: int, f: int) -> int:
        if u == t:
            return f
        e = self.it[u]
        while e != -1:
            self.it[u] = e
            v = self.to[e]
            if self.cap[e] > 0 and self.level[v] == self.level[u] + 1:
                pushed = self._dfs(v, t, min(f, self.cap[e]))
                if pushed:
                    self.cap[e] -= pushed
                    self.cap[e ^ 1] += pushed
                    return pushed
            e = self.nxt[e]
        self.it[u] = -1
        return 0

    def max_flow(self, s: int, t: int) -> int:
        flow = 0
        while self._bfs(s, t):
            self.it = list(self.head)
            while True:
                pushed = self._dfs(s, t, 1 << 400)
                if not pushed:
                    break
                flow += pushed
        return flow

    def reachable_from(self, s: int) -> list[bool]:
        """残量网络中从 s 可达的节点（最小割的源侧）。"""
        seen = [False] * self.n
        seen[s] = True
        queue = deque([s])
        while queue:
            u = queue.popleft()
            e = self.head[u]
            while e != -1:
                v = self.to[e]
                if self.cap[e] > 0 and not seen[v]:
                    seen[v] = True
                    queue.append(v)
                e = self.nxt[e]
        return seen


def _reachable_after(
    rects: list[Rect], removed: set[int], seeds: list[int]
) -> set[int]:
    """清除 ``removed`` 后，从种子下标出发沿闭交集仍可达的下标集合。"""
    seen = {i for i in seeds if i not in removed}
    queue = deque(seen)
    while queue:
        u = queue.popleft()
        for v in range(len(rects)):
            if v not in seen and v not in removed and _intersect(rects[u], rects[v]):
                seen.add(v)
                queue.append(v)
    return seen


def _reachable_ids(
    rects: list[Rect],
    removed: set[int],
    start_cover: list[int],
) -> list[str]:
    """清除 ``removed`` 后，从起点覆盖框出发仍可达的框标识（升序）。"""
    return sorted(
        rects[i].id for i in _reachable_after(rects, removed, start_cover)
    )


def _min_cost_cut(
    rects: list[Rect],
    costs: list[int],
    start_cover: list[int],
    end_cover: list[int],
) -> tuple[int, list[int]]:
    """全局裁决：返回 ``(最小总代价, 规范清洗集合的矩形下标升序列表)``。

    规范 = 同代价下升序框标识列表字典序最小（由容量微扰一次最大流定出，
    结果唯一）。调用前须保证两点原本连通（最小割有限）。
    """
    n = len(rects)
    base = 1 << n  # 大于全部权之和 (2**n - 1)
    inf = base * (sum(costs) + 1)  # 严格大于任何可行割的微扰容量

    source = 2 * n
    sink = 2 * n + 1
    flow = _Dinic(2 * n + 2)

    # 按标识升序赋权：越早出现的标识，"列入清洗集合"的优先权指数级更高。
    weight = [0] * n
    for rank, idx in enumerate(sorted(range(n), key=lambda i: rects[i].id)):
        weight[idx] = 1 << (n - 1 - rank)

    for i in range(n):
        flow.add_edge(2 * i, 2 * i + 1, costs[i] * base)
    for i in range(n):
        ri = rects[i]
        for j in range(i + 1, n):
            if _intersect(ri, rects[j]):
                flow.add_edge(2 * i + 1, 2 * j, inf)
                flow.add_edge(2 * j + 1, 2 * i, inf)
    for i in start_cover:
        flow.add_edge(source, 2 * i, inf)
    for i in end_cover:
        flow.add_edge(2 * i + 1, sink, inf)
    # 规范微扰：v 未被清洗（v_in、v_out 同侧）时恰有一条权边跨过割，
    # 故最小化权之和 = 最小化"未清洗"向量 = 清洗列表字典序最小。
    for i in range(n):
        flow.add_edge(source, 2 * i, weight[i])
        flow.add_edge(2 * i + 1, sink, weight[i])

    total = flow.max_flow(source, sink)
    # 微扰构造保证 total = 最小总代价 * base + 规范集合的 premium 和，
    # 且 premium 和 < base，故整除即得最小总代价。
    min_cost = total // base

    side = flow.reachable_from(source)
    cut = [i for i in range(n) if side[2 * i] and not side[2 * i + 1]]
    assert sum(costs[i] for i in cut) == min_cost
    return min_cost, cut


def separation_audit(
    rects: list[Rect],
    costs: list[int],
    start: tuple[int, int],
    end: tuple[int, int],
) -> dict:
    """对已校验输入执行隔离审计，返回稳定结论（不含部分方案）。"""
    start_cover = [i for i, r in enumerate(rects) if _covers(r, start)]
    if not start_cover:
        return {"status": STATUS_POINT_NOT_COVERED, "point": "start"}
    end_cover = [i for i, r in enumerate(rects) if _covers(r, end)]
    if not end_cover:
        return {"status": STATUS_POINT_NOT_COVERED, "point": "end"}

    # 两点原本不连通：无需清洗即已隔离，结论稳定、无部分方案。
    if not _reachable_after(rects, set(), start_cover) & set(end_cover):
        return {
            "status": STATUS_ALREADY_SEPARATED,
            "cleaned": [],
            "total_cost": "0",
            "reachable_from_start": _reachable_ids(rects, set(), start_cover),
        }

    min_cost, cut = _min_cost_cut(rects, costs, start_cover, end_cover)
    removed = set(cut)
    # 复核隔离证据：清除后起点可达集不得再触及任何终点覆盖框。
    assert not (_reachable_after(rects, removed, start_cover) & set(end_cover))
    return {
        "status": STATUS_CLEANED,
        "cleaned": sorted(rects[i].id for i in cut),
        "total_cost": str(min_cost),
        "reachable_from_start": _reachable_ids(rects, removed, start_cover),
    }
