"""隔离审计测试：固定构型 + 随机用例对照子集穷举预言机。

覆盖：相切连通（共边/共角点）、端点多重覆盖、同代价规范裁决、
几何重复框分别计价、未覆盖/本不连通的稳定结论、非法输入带位置拒绝。
"""

import itertools
import random
import unittest

from app.geometry import GeometryError, Rect
from app.separation import (
    parse_separation_payload,
    separation_audit,
)


def _covers(rect, point):
    px, py = point
    return rect.x1 <= px <= rect.x2 and rect.y1 <= py <= rect.y2


def _intersect(a, b):
    return (
        a.x1 <= b.x2 and b.x1 <= a.x2
        and a.y1 <= b.y2 and b.y1 <= a.y2
    )


def brute_force(rects, costs, start, end):
    """枚举全部子集求 (最小总代价, 规范清洗标识列表, 清除后可达标识)。

    规范 = 同代价下升序标识列表字典序最小。仅用于小规模对照。
    """
    n = len(rects)
    start_cover = [i for i, r in enumerate(rects) if _covers(r, start)]
    end_cover = [i for i, r in enumerate(rects) if _covers(r, end)]
    if not start_cover:
        return {"status": "point_not_covered", "point": "start"}
    if not end_cover:
        return {"status": "point_not_covered", "point": "end"}

    def reachable(removed):
        seen = {i for i in start_cover if i not in removed}
        stack = list(seen)
        while stack:
            u = stack.pop()
            for v in range(n):
                if (
                    v not in seen
                    and v not in removed
                    and _intersect(rects[u], rects[v])
                ):
                    seen.add(v)
                    stack.append(v)
        return seen

    if not (reachable(set()) & set(end_cover)):
        return {
            "status": "already_separated",
            "cleaned": [],
            "total_cost": "0",
            "reachable_from_start": sorted(rects[i].id for i in reachable(set())),
        }

    best_cost = None
    best_ids = None
    for mask in range(1 << n):
        removed = {i for i in range(n) if mask >> i & 1}
        cost = sum(costs[i] for i in removed)
        if best_cost is not None and cost > best_cost:
            continue
        if reachable(removed) & set(end_cover):
            continue  # 未切断
        ids = sorted(rects[i].id for i in removed)
        if best_cost is None or cost < best_cost or ids < best_ids:
            best_cost = cost
            best_ids = ids
    best_set = {i for i in range(n) if rects[i].id in set(best_ids)}
    return {
        "status": "cleaned",
        "cleaned": best_ids,
        "total_cost": str(best_cost),
        "reachable_from_start": sorted(
            rects[i].id for i in reachable(best_set)
        ),
    }


class FixedSeparationTest(unittest.TestCase):
    def test_tangent_chain_clean_middle(self):
        # 共边相切即连通：a-b-c 链，清洗 b 最便宜。
        result = separation_audit(
            [Rect("a", 0, 0, 1, 1), Rect("b", 1, 0, 2, 1), Rect("c", 2, 0, 3, 1)],
            [5, 2, 9],
            (0, 0),
            (2, 0),
        )
        self.assertEqual(
            result,
            {
                "status": "cleaned",
                "cleaned": ["b"],
                "total_cost": "2",
                "reachable_from_start": ["a"],
            },
        )

    def test_corner_touch_connects(self):
        # 仅角点接触也连通：两点分别落在两个角接触框上，必须清洗其一。
        result = separation_audit(
            [Rect("a", 0, 0, 2, 2), Rect("b", 2, 2, 4, 4)],
            [3, 7],
            (0, 0),
            (4, 4),
        )
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(result["cleaned"], ["a"])
        self.assertEqual(result["total_cost"], "3")
        self.assertEqual(result["reachable_from_start"], [])

    def test_gap_means_already_separated(self):
        result = separation_audit(
            [Rect("a", 0, 0, 1, 1), Rect("b", 3, 0, 4, 1)],
            [5, 5],
            (0, 0),
            (3, 0),
        )
        self.assertEqual(
            result,
            {
                "status": "already_separated",
                "cleaned": [],
                "total_cost": "0",
                "reachable_from_start": ["a"],
            },
        )

    def test_endpoint_multi_coverage_all_must_go(self):
        # 起点被 m1、m2 同时覆盖：切断须把两者都清除。
        result = separation_audit(
            [
                Rect("m1", 0, 0, 2, 2),
                Rect("m2", 0, 0, 2, 2),
                Rect("b", 2, 0, 3, 2),
            ],
            [4, 6, 1],
            (1, 1),
            (2, 0),
        )
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(result["cleaned"], ["m1", "m2"])
        self.assertEqual(result["total_cost"], "10")
        self.assertEqual(result["reachable_from_start"], [])

    def test_geometric_duplicates_priced_separately(self):
        # 几何重复框作为可分别清除的来源：重复不合并、不免费。
        result = separation_audit(
            [Rect("m1", 0, 0, 2, 2), Rect("m2", 0, 0, 2, 2)],
            [4, 6],
            (1, 1),
            (2, 2),
        )
        self.assertEqual(result["cleaned"], ["m1", "m2"])
        self.assertEqual(result["total_cost"], "10")

    def test_same_cost_canonical_lexicographic(self):
        # 菱形双通道 s-m1-t / s-m2-t（m1、m2 之间留有 y 间隙不连通）：
        # {s} 与 {m1,m2} 都是代价 10 的最小割，长度不同——
        # 规范按逐元素字典序：["m1","m2"] < ["s"]。
        rects = [
            Rect("s", 0, 0, 1, 5),
            Rect("m1", 1, 3, 4, 5),
            Rect("m2", 1, 0, 4, 2),
            Rect("t", 4, 0, 5, 5),
        ]
        result = separation_audit(rects, [10, 5, 5, 20], (0, 2), (4, 2))
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(result["cleaned"], ["m1", "m2"])
        self.assertEqual(result["total_cost"], "10")
        # 复核隔离证据：t 与 m1/m2 从起点不可达；s 仍可达。
        self.assertEqual(result["reachable_from_start"], ["s"])

    def test_canonical_uses_id_order_not_index_order(self):
        # 链 s-q-a-t：单割 q（下标 1）或 a（下标 2）等价，
        # 规范按标识字典序 -> ["a"]，与数组下标顺序相反。
        rects = [
            Rect("s", 0, 0, 1, 1),
            Rect("q", 1, 0, 2, 1),
            Rect("a", 2, 0, 3, 1),
            Rect("t", 3, 0, 4, 1),
        ]
        result = separation_audit(rects, [99, 5, 5, 99], (0, 0), (3, 0))
        self.assertEqual(result["cleaned"], ["a"])
        self.assertEqual(result["total_cost"], "5")
        self.assertEqual(result["reachable_from_start"], ["q", "s"])

    def test_point_not_covered_stable_conclusion(self):
        rects = [Rect("a", 0, 0, 1, 1), Rect("b", 1, 0, 2, 1)]
        self.assertEqual(
            separation_audit(rects, [1, 1], (9, 9), (0, 0)),
            {"status": "point_not_covered", "point": "start"},
        )
        self.assertEqual(
            separation_audit(rects, [1, 1], (0, 0), (9, 9)),
            {"status": "point_not_covered", "point": "end"},
        )

    def test_point_on_boundary_counts_covered(self):
        result = separation_audit(
            [Rect("a", 0, 0, 2, 2), Rect("b", 2, 0, 4, 2)],
            [1, 1],
            (2, 2),  # a 的边界角点
            (4, 0),
        )
        self.assertEqual(result["status"], "cleaned")


class BruteForceFuzzTest(unittest.TestCase):
    def test_random_instances_match_oracle(self):
        rng = random.Random(20260924)
        for trial in range(250):
            n = rng.randint(1, 9)
            rects = []
            costs = []
            # 标识与数组下标故意乱序，确保规范裁决按标识字典序。
            id_pool = [f"id{k:02d}" for k in range(n)]
            rng.shuffle(id_pool)
            for k in range(n):
                x1 = rng.randint(-4, 3)
                x2 = rng.randint(x1 + 1, x1 + 4)
                y1 = rng.randint(-4, 3)
                y2 = rng.randint(y1 + 1, y1 + 4)
                rects.append(Rect(id_pool[k], x1, y1, x2, y2))
                costs.append(rng.randint(1, 9))
            start = (rng.randint(-4, 6), rng.randint(-4, 6))
            end = (rng.randint(-4, 6), rng.randint(-4, 6))
            with self.subTest(trial=trial, rects=rects, costs=costs):
                self.assertEqual(
                    separation_audit(rects, costs, start, end),
                    brute_force(rects, costs, start, end),
                )


class PayloadValidationTest(unittest.TestCase):
    def _must_fail(self, payload):
        try:
            parse_separation_payload(payload)
        except GeometryError as exc:
            return exc
        raise AssertionError("expected GeometryError")

    def test_accepts_valid_payload(self):
        rects, costs, start, end = parse_separation_payload(
            {
                "rectangles": [
                    {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 3}
                ],
                "start": {"x": 0, "y": 0},
                "end": {"x": 1, "y": 1},
            }
        )
        self.assertEqual(costs, [3])
        self.assertEqual((start, end), ((0, 0), (1, 1)))
        self.assertEqual(rects[0].id, "a")

    def test_rejects_non_object_and_missing_fields(self):
        exc = self._must_fail([1, 2, 3])
        self.assertEqual(exc.code, "invalid_payload")
        exc = self._must_fail({"start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 1}})
        self.assertEqual(exc.code, "invalid_payload")

    def test_rejects_bad_cost_with_location(self):
        for bad in (0, -3, 1.5, "2", None, True, 10**9 + 1):
            exc = self._must_fail(
                {
                    "rectangles": [
                        {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": bad}
                    ],
                    "start": {"x": 0, "y": 0},
                    "end": {"x": 1, "y": 1},
                }
            )
            self.assertEqual(exc.code, "invalid_rectangle")
            self.assertEqual(exc.location, {"index": 0, "id": "a"})
            self.assertIn("cost", str(exc))

    def test_rejects_bad_point_with_field(self):
        base = {
            "rectangles": [
                {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1}
            ],
            "start": {"x": 0, "y": 0},
            "end": {"x": 1, "y": 1},
        }
        for bad_point in (None, [0, 0], {"x": 0}, {"x": 0.5, "y": 0},
                          {"x": True, "y": 0}, {"x": 0, "y": 10**9 + 1}):
            payload = {**base, "end": bad_point}
            exc = self._must_fail(payload)
            self.assertEqual(exc.code, "invalid_point")
            self.assertEqual(exc.location, {"field": "end"})

    def test_count_limit_160(self):
        records = [
            {"id": f"r{i}", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1}
            for i in range(161)
        ]
        exc = self._must_fail(
            {"rectangles": records,
             "start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 1}}
        )
        self.assertIn("1 and 160", str(exc))
        # 160 个合法。
        parse_separation_payload(
            {"rectangles": records[:160],
             "start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 1}}
        )

    def test_duplicate_id_still_rejected(self):
        exc = self._must_fail(
            {
                "rectangles": [
                    {"id": "x", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 1},
                    {"id": "x", "x1": 2, "y1": 2, "x2": 3, "y2": 3, "cost": 1},
                ],
                "start": {"x": 0, "y": 0},
                "end": {"x": 2, "y": 2},
            }
        )
        self.assertEqual(exc.location, {"index": 1, "id": "x"})


class PerformanceTest(unittest.TestCase):
    def test_dense_160_rectangles(self):
        import time

        rects = []
        costs = []
        for k in range(160):
            x1 = (k * 7) % 9
            y1 = (k * 11) % 9
            rects.append(Rect(f"r{k:03d}", x1, y1, x1 + 20, y1 + 20))
            costs.append((k * 13) % 97 + 1)
        # 所有框都覆盖 (10,10)：稠密图、两端多重覆盖，必须清掉全部覆盖框。
        start = time.perf_counter()
        result = separation_audit(rects, costs, (10, 10), (10, 10))
        elapsed = time.perf_counter() - start
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(len(result["cleaned"]), 160)
        self.assertEqual(result["total_cost"], str(sum(costs)))
        self.assertEqual(result["reachable_from_start"], [])
        self.assertLess(elapsed, 5.0, f"separation too slow: {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
