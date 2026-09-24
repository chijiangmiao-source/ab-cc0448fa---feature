"""隔离裁决测试：小规模子集枚举暴力预言机 + 相切/多重覆盖等固定构型。"""

import itertools
import random
import unittest

from app.geometry import Rect
from app.separation import Point, separate


def _closed_intersect(a, b):
    return (
        max(a.x1, b.x1) <= min(a.x2, b.x2)
        and max(a.y1, b.y1) <= min(a.y2, b.y2)
    )


def brute_optimum(rects, costs, start, end):
    """枚举全部清除子集，返回 (status, removed_ids_sorted, cost) 预言机结果。"""
    n = len(rects)

    def covers(r, p):
        return r.x1 <= p.x <= r.x2 and r.y1 <= p.y <= r.y2

    cs = [i for i, r in enumerate(rects) if covers(r, start)]
    ce = [i for i, r in enumerate(rects) if covers(r, end)]
    if not cs:
        return ("not_covered", None, None)
    if not ce:
        return ("not_covered", None, None)

    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if _closed_intersect(rects[i], rects[j]):
                adj[i].append(j)
                adj[j].append(i)

    def connected(blocked):
        roots = [i for i in cs if i not in blocked]
        seen = set(roots)
        stack = list(roots)
        targets = set(ce) - blocked
        while stack:
            v = stack.pop()
            if v in targets:
                return True
            for w in adj[v]:
                if w not in blocked and w not in seen:
                    seen.add(w)
                    stack.append(w)
        return False

    if not connected(frozenset()):
        return ("already_separated", None, None)

    best_cost = None
    best_ids = None
    for mask in range(1 << n):
        blocked = frozenset(i for i in range(n) if mask >> i & 1)
        if connected(blocked):
            continue
        cost = sum(costs[i] for i in blocked)
        ids = tuple(sorted(rects[i].id for i in blocked))
        key = (cost, ids)
        if best_cost is None or key < (best_cost, best_ids):
            best_cost, best_ids = key
    return ("separated", list(best_ids), best_cost)


def _reachable(rects, removed_ids, start):
    n = len(rects)
    by_id = {r.id: i for i, r in enumerate(rects)}
    blocked = {by_id[x] for x in removed_ids}

    def covers(r, p):
        return r.x1 <= p.x <= r.x2 and r.y1 <= p.y <= r.y2

    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if _closed_intersect(rects[i], rects[j]):
                adj[i].append(j)
                adj[j].append(i)
    roots = [
        i for i, r in enumerate(rects)
        if covers(r, start) and i not in blocked
    ]
    seen = set(roots)
    stack = list(roots)
    while stack:
        v = stack.pop()
        for w in adj[v]:
            if w not in blocked and w not in seen:
                seen.add(w)
                stack.append(w)
    return sorted(rects[i].id for i in seen)


class FixedSeparationTest(unittest.TestCase):
    def test_chain_pick_cheapest_middle(self):
        # 共边链 a-b-c：两端贵、中间便宜，全局割必须选中 b。
        rects = [
            Rect("a", 0, 0, 1, 1),
            Rect("b", 1, 0, 2, 1),
            Rect("c", 2, 0, 3, 1),
        ]
        result = separate(rects, [5, 1, 5], Point(0, 0), Point(3, 0))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["b"])
        self.assertEqual(result["total_cost"], "1")
        self.assertEqual(result["reachable_from_start"], ["a"])

    def test_tangent_edge_is_connected(self):
        # 仅共边：按闭合交集必须连通；切任一框即可。
        rects = [Rect("a", 0, 0, 2, 3), Rect("b", 2, 0, 4, 3)]
        result = separate(rects, [7, 2], Point(0, 0), Point(4, 0))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["b"])
        self.assertEqual(result["total_cost"], "2")

    def test_corner_touch_is_connected(self):
        # 仅角点接触：面积重叠建图会误判不连通，闭合交图必须连通。
        rects = [Rect("a", 0, 0, 1, 1), Rect("b", 1, 1, 2, 2)]
        result = separate(rects, [1, 1], Point(0, 0), Point(2, 2))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["a"])

    def test_corner_touch_no_route_otherwise(self):
        # 角点是唯一通路：不按闭合交集就无法发现这条连通。
        rects = [
            Rect("a", 0, 0, 2, 2),
            Rect("b", 2, 2, 4, 4),
            Rect("c", 4, 4, 6, 6),
        ]
        result = separate(rects, [9, 1, 9], Point(0, 0), Point(6, 6))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["b"])
        self.assertEqual(result["total_cost"], "1")

    def test_endpoint_multiple_coverage(self):
        # 起点被两个框多重覆盖，必须同时切断两路（单割任一不够）。
        rects = [
            Rect("a", 0, 0, 3, 3),
            Rect("b", 0, 0, 2, 2),
            Rect("c", 3, 3, 5, 5),
        ]
        # a 与 c 角点相接 (3,3)；b 与 c 不相交。通路只有 a-c。
        result = separate(rects, [50, 1, 1], Point(1, 1), Point(5, 5))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["c"])
        self.assertEqual(sorted(result["reachable_from_start"]), ["a", "b"])

    def test_endpoint_multiple_coverage_two_routes(self):
        # 起点 (0,1) 落在 a/b 的共边上、终点 (4,1) 落在 c/d 的共边上，
        # 四框在 (2,1) 处两两相接：必须同时切掉两个端点覆盖框（任一路幸存
        # 都仍可达终点），贵的一端保留、切便宜的一端 {c,d}。
        rects = [
            Rect("a", 0, 0, 2, 1),
            Rect("b", 0, 1, 2, 2),
            Rect("c", 2, 0, 4, 1),
            Rect("d", 2, 1, 4, 2),
        ]
        result = separate(
            rects, [100, 100, 1, 1], Point(0, 1), Point(4, 1)
        )
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["c", "d"])
        self.assertEqual(result["total_cost"], "2")
        self.assertEqual(result["reachable_from_start"], ["a", "b"])

    def test_geometric_duplicates_are_separate_sources(self):
        # 两个几何完全重复的框：必须两个都清除才断连，代价求和。
        rects = [
            Rect("a", 0, 0, 2, 2),
            Rect("b", 0, 0, 2, 2),
        ]
        result = separate(rects, [3, 4], Point(0, 0), Point(2, 2))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["a", "b"])
        self.assertEqual(result["total_cost"], "7")
        self.assertEqual(result["reachable_from_start"], [])

    def test_geometric_duplicates_cheaper_pair(self):
        # 起点与终点间有两条完全重合的重复框通道：切两对重复框中较宜的。
        rects = [
            Rect("s", 0, 0, 1, 1),
            Rect("a", 1, 0, 2, 1),
            Rect("b", 1, 0, 2, 1),
            Rect("t", 2, 0, 3, 1),
        ]
        result = separate(rects, [100, 2, 3, 100], Point(0, 0), Point(3, 0))
        self.assertEqual(result["removed"], ["a", "b"])
        self.assertEqual(result["total_cost"], "5")

    def test_equal_cost_lexicographic_tie(self):
        # 共边链上切任一单框都可断连，同代价取字典序最小标识。
        rects = [
            Rect("a", 0, 0, 1, 1),
            Rect("b", 1, 0, 2, 1),
            Rect("c", 2, 0, 3, 1),
        ]
        result = separate(rects, [5, 5, 5], Point(0, 0), Point(3, 0))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["a"])

    def test_equal_cost_lex_tie_numeric_like_ids(self):
        # 字典序按字符串码位而非数值：'r10' 排在 'r2'/'r3' 之前。
        rects = [
            Rect("r2", 0, 0, 1, 1),
            Rect("r10", 1, 0, 2, 1),
            Rect("r3", 2, 0, 3, 1),
        ]
        result = separate(rects, [5, 5, 5], Point(0, 0), Point(3, 0))
        self.assertEqual(result["removed"], ["r10"])

    def test_lex_tie_between_different_set_shapes(self):
        # 漏斗构型：s-a-{b,c 并行}-t。
        # 割 {a}（上游咽喉）与割 {b,c}（下游两道并行）同成本时，
        # 比较升序标识列表 ['a'] vs ['b','c'] -> 选 a。
        rects = [
            Rect("s", 0, 0, 4, 1),
            Rect("a", 1, 1, 3, 2),
            Rect("b", 1, 2, 2, 3),
            Rect("c", 2, 2, 3, 3),
            Rect("t", 1, 3, 3, 4),
        ]
        # s 只接 a；a 分接 b、c；b、c 同接 t；a 与 t 间隔一行不相交。
        result = separate(rects, [100, 4, 2, 2, 100], Point(2, 0), Point(2, 4))
        self.assertEqual(result["removed"], ["a"])
        self.assertEqual(result["total_cost"], "4")
        self.assertEqual(result["reachable_from_start"], ["s"])
        # 反向：a 更贵 -> 切 {b,c}。
        result = separate(rects, [100, 5, 2, 2, 100], Point(2, 0), Point(2, 4))
        self.assertEqual(result["removed"], ["b", "c"])
        self.assertEqual(result["total_cost"], "4")
        self.assertEqual(result["reachable_from_start"], ["a", "s"])

    def test_start_not_covered_stable_conclusion(self):
        rects = [Rect("a", 1, 1, 2, 2)]
        result = separate(rects, [1], Point(0, 0), Point(1, 1))
        self.assertEqual(
            result,
            {"status": "not_covered", "point": "start", "reason": "start_not_covered"},
        )
        self.assertNotIn("removed", result)

    def test_end_not_covered_stable_conclusion(self):
        rects = [Rect("a", 0, 0, 1, 1)]
        result = separate(rects, [1], Point(0, 0), Point(5, 5))
        self.assertEqual(result["status"], "not_covered")
        self.assertEqual(result["point"], "end")
        self.assertNotIn("removed", result)

    def test_points_on_boundary_count_as_covered(self):
        # 取样点落在框边界上也算被覆盖；起点与终点为同一个角点 (2,2)，
        # 同时被两个仅角点相接的框覆盖：必须两个都清除。
        rects = [Rect("a", 0, 0, 2, 2), Rect("b", 2, 2, 4, 4)]
        result = separate(rects, [1, 1], Point(2, 2), Point(2, 2))
        self.assertEqual(result["status"], "separated")
        self.assertEqual(result["removed"], ["a", "b"])
        self.assertEqual(result["total_cost"], "2")
        self.assertEqual(result["reachable_from_start"], [])

    def test_already_separated_no_partial_plan(self):
        rects = [Rect("a", 0, 0, 1, 1), Rect("b", 5, 5, 6, 6)]
        result = separate(rects, [1, 1], Point(0, 0), Point(6, 6))
        self.assertEqual(
            result,
            {"status": "already_separated", "reason": "points_not_connected"},
        )
        self.assertNotIn("removed", result)
        self.assertNotIn("total_cost", result)

    def test_reachable_partition_is_evidence(self):
        rects = [
            Rect("a", 0, 0, 2, 2),
            Rect("b", 2, 0, 4, 2),
            Rect("c", 4, 0, 6, 2),
        ]
        result = separate(rects, [1, 1, 1], Point(0, 0), Point(6, 0))
        self.assertEqual(result["removed"], ["a"])  # 字典序最小单框割
        self.assertEqual(result["reachable_from_start"], [])
        result = separate(rects, [100, 1, 100], Point(0, 0), Point(6, 0))
        self.assertEqual(result["removed"], ["b"])
        self.assertEqual(result["reachable_from_start"], ["a"])


class BruteForceFuzzTest(unittest.TestCase):
    def test_random_small_cases_match_enumeration(self):
        rng = random.Random(20260924)
        for trial in range(400):
            n = rng.randint(1, 7)
            rects = []
            for k in range(n):
                x1 = rng.randint(0, 4)
                x2 = x1 + rng.randint(1, 3)
                y1 = rng.randint(0, 4)
                y2 = y1 + rng.randint(1, 3)
                rects.append(Rect(f"r{k}", x1, y1, x2, y2))
            costs = [rng.randint(1, 5) for _ in range(n)]
            start = Point(rng.randint(0, 6), rng.randint(0, 6))
            end = Point(rng.randint(0, 6), rng.randint(0, 6))

            with self.subTest(trial=trial, rects=rects, start=start, end=end):
                got = separate(rects, costs, start, end)
                status, ids, cost = brute_optimum(rects, costs, start, end)
                self.assertEqual(got["status"], status)
                if status == "separated":
                    self.assertEqual(got["removed"], ids)
                    self.assertEqual(got["total_cost"], str(cost))
                    # 分区复核：与按清除集重算的起点闭包一致，且不含终点覆盖框。
                    self.assertEqual(
                        got["reachable_from_start"],
                        _reachable(rects, ids, start),
                    )
                    end_ids = {
                        r.id for r in rects
                        if r.x1 <= end.x <= r.x2 and r.y1 <= end.y <= r.y2
                    }
                    self.assertFalse(
                        set(got["reachable_from_start"]) & end_ids
                    )


class ScaleTest(unittest.TestCase):
    def test_160_rects_timing(self):
        import time

        rng = random.Random(99)
        rects = []
        for k in range(160):
            x1 = rng.randint(0, 100)
            y1 = rng.randint(0, 100)
            rects.append(
                Rect(f"id-{k:03d}", x1, y1, x1 + rng.randint(2, 8), y1 + rng.randint(2, 8))
            )
        costs = [rng.randint(1, 1000) for _ in rects]
        start = time.perf_counter()
        result = separate(rects, costs, Point(0, 0), Point(105, 105))
        elapsed = time.perf_counter() - start
        self.assertIn(result["status"], ("separated", "already_separated", "not_covered"))
        self.assertLess(elapsed, 10.0, f"separation too slow: {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
