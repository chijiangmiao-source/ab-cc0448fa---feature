"""取样点隔离裁决：闭合矩形交图上的全局最小代价顶点割。

输入与 ``/api/audit`` 同一组合法矩形（额外要求每个框带正整数 ``cost``），
外加两个整数取样点 ``start`` / ``end``，框数上限 160。

连通关系
--------
两个框的**闭合**矩形交集非空即连通——共边（一维相接）与共点（角点相接）
都算连通；只有面积重叠才算连通的建图方式在此被明确排除。几何重复的框
交集为整个矩形，它们互为独立顶点，可分别作为清洗来源。

全局裁决
--------
在"框相交图"上求起点覆盖集到终点覆盖集的最小权顶点割。顶点拆边：

* 框 i 拆为 ``in_i -> out_i``，容量为其清洗代价；
* 相交框对加 ``out_i -> in_j`` 与 ``out_j -> in_i``，容量 INF；
* 覆盖起点的框由超源 S 以 INF 接入 ``in``，覆盖终点的框由 ``out`` 以
  INF 接入超汇 T。

任一 s-t 割与"一组被清除的框"一一对应，故一次最大流给出的最小割就是
全局精确最小总代价——不逐次删除、不局部贪心、不以面积重叠代替交图。

同代价字典序裁决
----------------
"升序框标识列表的字典序"不能写成容量上的线性位权（可证伪：空集 <
{a} < {a,b} < {b} 与任何加性权重都矛盾）。因此先用一次全局最大流定出
最小代价 C，再以**精确可行性问答**逐位构造字典序最小的列表：

* 强制某框保留——把其拆边容量置为 INF，使其无法入割；
* 强制某框清除——加 ``S -> in_i`` 与 ``out_i -> T`` 的 INF 边，
  迫使其跨割；
* 问答"该约束下最小割是否仍为 C"仍是在**完整图**上做全局最大流，
  不删除任何框、不做近似；约束只收紧可行割集合。

每一问都是全局裁决；至多 O(n) 问，n ≤ 160。Python 大整数保证精确，
零第三方依赖。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .geometry import GeometryError, Rect

MAX_RECTS = 160
COORD_BOUND = 1_000_000_000


@dataclass(frozen=True)
class Point:
    x: int
    y: int


class _Dinic:
    """大整数容量的 Dinic 最大流（纯标准库）。"""

    __slots__ = ("n", "g", "level", "it")

    def __init__(self, n: int) -> None:
        self.n = n
        self.g: list[list[list[int]]] = [[] for _ in range(n)]

    def add_edge(self, u: int, v: int, cap: int) -> None:
        # [对端, 反向边下标, 剩余容量]
        self.g[u].append([v, len(self.g[v]), cap])
        self.g[v].append([u, len(self.g[u]) - 1, 0])

    def _bfs(self, s: int, t: int) -> bool:
        self.level = [-1] * self.n
        self.level[s] = 0
        q = deque([s])
        g = self.g
        while q:
            v = q.popleft()
            nv = self.level[v] + 1
            for to, _rev, cap in g[v]:
                if cap > 0 and self.level[to] < 0:
                    self.level[to] = nv
                    if to == t:
                        return True
                    q.append(to)
        return self.level[t] >= 0

    def _dfs(self, v: int, t: int, pushed: int) -> int:
        if v == t:
            return pushed
        g = self.g
        while self.it[v] < len(g[v]):
            e = g[v][self.it[v]]
            to, rev, cap = e
            if cap > 0 and self.level[to] == self.level[v] + 1:
                d = self._dfs(to, t, min(pushed, cap))
                if d:
                    e[2] -= d
                    g[to][rev][2] += d
                    return d
            self.it[v] += 1
        return 0

    def max_flow(self, s: int, t: int, stop_above: int | None = None) -> int:
        flow = 0
        while self._bfs(s, t):
            self.it = [0] * self.n
            while True:
                pushed = self._dfs(s, t, 10**100)
                if not pushed:
                    break
                flow += pushed
                # 可行性问答只需知道流值是否已超过已知最优割：超过即不可行。
                if stop_above is not None and flow > stop_above:
                    return flow
        return flow


def _closed_intersect(a: Rect, b: Rect) -> bool:
    """闭合矩形非空交集：共边、共点均相交。"""
    return (
        max(a.x1, b.x1) <= min(a.x2, b.x2)
        and max(a.y1, b.y1) <= min(a.y2, b.y2)
    )


def _covers(rect: Rect, p: Point) -> bool:
    return rect.x1 <= p.x <= rect.x2 and rect.y1 <= p.y <= rect.y2


def _build_flow(
    n: int,
    adj: list[list[int]],
    cover_start: list[int],
    cover_end: list[int],
    costs: list[int],
    forced_remove: frozenset[int],
    forced_keep: frozenset[int],
):
    """构造约束下的拆点网络；返回 (网络, S, T, INF)。

    强制清除的框：加 S->in、out->T 的 INF 边，逼拆边跨割；
    强制保留的框：拆边容量置 INF，使其无法入割。
    """
    inf = sum(costs) + 1
    net = _Dinic(2 * n + 2)
    s_node, t_node = 2 * n, 2 * n + 1

    def vin(i: int) -> int:
        return 2 * i

    def vout(i: int) -> int:
        return 2 * i + 1

    for i, w in enumerate(costs):
        net.add_edge(vin(i), vout(i), inf if i in forced_keep else w)
    for i in range(n):
        for j in adj[i]:
            if i < j:
                net.add_edge(vout(i), vin(j), inf)
                net.add_edge(vout(j), vin(i), inf)
    for i in cover_start:
        net.add_edge(s_node, vin(i), inf)
    for i in cover_end:
        net.add_edge(vout(i), t_node, inf)
    for i in forced_remove:
        net.add_edge(s_node, vin(i), inf)
        net.add_edge(vout(i), t_node, inf)
    return net, s_node, t_node, inf


def _feasible(
    n,
    adj,
    cover_start,
    cover_end,
    costs,
    optimum: int,
    forced_remove: frozenset[int],
    forced_keep: frozenset[int],
) -> bool:
    """该约束下是否仍存在总代价恰为 optimum 的割（完整图上的全局问答）。"""
    net, s_node, t_node, inf = _build_flow(
        n, adj, cover_start, cover_end, costs, forced_remove, forced_keep
    )
    value = net.max_flow(s_node, t_node, stop_above=optimum)
    return value == optimum  # 不可行时流值 ≥ INF > 任意真实割代价


def separate(
    rects: list[Rect], costs: list[int], start: Point, end: Point
) -> dict:
    """计算隔离方案，返回稳定结论字典（见模块文档与 README）。"""
    n = len(rects)

    cover_start = [i for i, r in enumerate(rects) if _covers(r, start)]
    if not cover_start:
        return {
            "status": "not_covered",
            "point": "start",
            "reason": "start_not_covered",
        }
    cover_end = [i for i, r in enumerate(rects) if _covers(r, end)]
    if not cover_end:
        return {
            "status": "not_covered",
            "point": "end",
            "reason": "end_not_covered",
        }

    # 闭合交图（O(n^2)，n ≤ 160）。
    adj: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        ri = rects[i]
        for j in range(i + 1, n):
            if _closed_intersect(ri, rects[j]):
                adj[i].append(j)
                adj[j].append(i)

    # 先判原本连通性：不连通则无方案可给。
    reached = [False] * n
    stack = list(cover_start)
    for i in stack:
        reached[i] = True
    targets = set(cover_end)
    connected = False
    while stack:
        v = stack.pop()
        if v in targets:
            connected = True
            break
        for w in adj[v]:
            if not reached[w]:
                reached[w] = True
                stack.append(w)
    if not connected:
        return {
            "status": "already_separated",
            "reason": "points_not_connected",
        }

    empty = frozenset()
    net, s_node, t_node, _inf = _build_flow(
        n, adj, cover_start, cover_end, costs, empty, empty
    )
    optimum = net.max_flow(s_node, t_node)

    # 标识按字典序排序后的索引顺序（id 为字符串，按 Unicode 码位）。
    order = sorted(range(n), key=lambda i: rects[i].id)

    def ask(removed: frozenset[int], kept: frozenset[int]) -> bool:
        return _feasible(
            n, adj, cover_start, cover_end, costs, optimum, removed, kept
        )

    # 逐位构造升序标识列表：已选前缀 removed，candidate 之前的未选框
    # 全部强制保留；先试"列表就此结束"，再按标识升序试下一个元素。
    chosen: list[int] = []
    removed: set[int] = set()
    kept: set[int] = set()
    pos = 0
    while pos < len(order):
        # 尝试收尾：剩余框全部保留时是否仍有最优割。
        tail_kept = kept | set(order[pos:])
        if ask(frozenset(removed), frozenset(tail_kept)):
            break
        # 否则找最小的可作为下一列表元素的标识。
        for k in range(pos, len(order)):
            cand = order[k]
            between = set(order[pos:k])  # 必须在列表中缺席 -> 强制保留
            if ask(
                frozenset(removed | {cand}),
                frozenset(kept | between),
            ):
                chosen.append(cand)
                removed.add(cand)
                kept.update(between)
                pos = k + 1
                break
        else:  # 理论不可达：收尾不可行则必有某个元素可行
            raise RuntimeError("tie-break feasibility search exhausted")

    removed_set = frozenset(chosen)
    total = sum(costs[i] for i in chosen)

    # 清除后从起点仍可达的框：以幸存的起点覆盖框为根做闭包，
    # 作为隔离证据分区（任何幸存的终点覆盖框都不得落入其中）。
    keep = [False] * n
    stack = [i for i in cover_start if i not in removed_set]
    for i in stack:
        keep[i] = True
    while stack:
        v = stack.pop()
        for w in adj[v]:
            if w not in removed_set and not keep[w]:
                keep[w] = True
                stack.append(w)

    return {
        "status": "separated",
        "removed": [rects[i].id for i in chosen],
        "total_cost": str(total),
        "reachable_from_start": sorted(rects[i].id for i in range(n) if keep[i]),
    }


def _parse_point(obj: object, name: str) -> Point:
    loc = {"point": name}
    if not isinstance(obj, dict):
        raise GeometryError(
            f"'{name}' must be an object with integer 'x' and 'y'",
            loc,
            "invalid_point",
        )
    coords = {}
    for key in ("x", "y"):
        val = obj.get(key)
        # 拒绝 bool（bool 是 int 子类）：取样点必须为整数。
        if isinstance(val, bool) or not isinstance(val, int):
            raise GeometryError(
                f"'{name}.{key}' must be an integer", loc, "invalid_point"
            )
        coords[key] = val
    return Point(coords["x"], coords["y"])


def separation_raw(payload: object) -> dict:
    """校验载荷并执行隔离裁决。

    形如::

        {
          "rectangles": [
            {"id": "a", "x1": 0, "y1": 0, "x2": 2, "y2": 2, "cost": 3},
            ...
          ],
          "start": {"x": 0, "y": 0},
          "end":   {"x": 9, "y": 9}
        }

    任何非法输入都抛出带位置的 ``GeometryError``，不产生部分结果。
    """
    if not isinstance(payload, dict):
        raise GeometryError(
            "request body must be a JSON object with 'rectangles', "
            "'start' and 'end'",
            None,
            "invalid_request",
        )
    records = payload.get("rectangles")
    if not isinstance(records, list):
        raise GeometryError("'rectangles' must be an array", None, "invalid_request")
    if not 1 <= len(records) <= MAX_RECTS:
        raise GeometryError(
            f"rectangle count must be between 1 and {MAX_RECTS}, "
            f"got {len(records)}",
            None,
            "invalid_request",
        )

    rects: list[Rect] = []
    costs: list[int] = []
    seen_ids: set[str] = set()
    required = ("x1", "y1", "x2", "y2")

    for pos, rec in enumerate(records):
        loc: dict = {"index": pos}
        where = f"rectangles[{pos}]"
        if not isinstance(rec, dict):
            raise GeometryError(f"{where}: must be an object", loc)
        rid = rec.get("id")
        if not isinstance(rid, str) or not rid:
            raise GeometryError(f"{where}: 'id' must be a non-empty string", loc)
        loc["id"] = rid
        if rid in seen_ids:
            raise GeometryError(
                f"{where}: duplicate id {rid!r} (id must be unique)", loc
            )
        seen_ids.add(rid)

        coords = {}
        for key in required:
            val = rec.get(key)
            # 拒绝 bool（bool 是 int 子类）与浮点：坐标必须为整数。
            if isinstance(val, bool) or not isinstance(val, int):
                raise GeometryError(
                    f"{where} (id={rid!r}): '{key}' must be an integer", loc
                )
            if abs(val) > COORD_BOUND:
                raise GeometryError(
                    f"{where} (id={rid!r}): '{key}'={val} out of range "
                    "[-1e9, 1e9]",
                    loc,
                )
            coords[key] = val
        if not coords["x1"] < coords["x2"]:
            raise GeometryError(
                f"{where} (id={rid!r}): require x1 < x2, got "
                f"x1={coords['x1']}, x2={coords['x2']}",
                loc,
            )
        if not coords["y1"] < coords["y2"]:
            raise GeometryError(
                f"{where} (id={rid!r}): require y1 < y2, got "
                f"y1={coords['y1']}, y2={coords['y2']}",
                loc,
            )

        cost = rec.get("cost")
        if isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
            raise GeometryError(
                f"{where} (id={rid!r}): 'cost' must be a positive integer",
                loc,
            )

        rects.append(
            Rect(rid, coords["x1"], coords["y1"], coords["x2"], coords["y2"])
        )
        costs.append(cost)

    if "start" not in payload:
        raise GeometryError("missing 'start' point", None, "invalid_request")
    if "end" not in payload:
        raise GeometryError("missing 'end' point", None, "invalid_request")
    start = _parse_point(payload["start"], "start")
    end = _parse_point(payload["end"], "end")

    return separate(rects, costs, start, end)
