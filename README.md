# 光刻版缺陷复核服务（photomask-defect-audit）

对轴对齐污染框集合计算**并集面积**与**外露周长**；并对两个取样点做
**最小代价清洗集合**的隔离审计。重叠、相切、完全包围都只计一次；内部
接缝（含覆盖 2→1 的缝）不计入清洗边界。

## 接口

### `POST /api/audit`

请求体为 1–50000 个矩形的 JSON 数组（也接受 `{"rectangles": [...]}`）：

```json
[
  {"id": "a", "x1": 0, "y1": 0, "x2": 3, "y2": 2},
  {"id": "b", "x1": 1, "y1": 1, "x2": 4, "y2": 3}
]
```

约束：`id` 为非空字符串且**全局唯一**（几何重复允许、标识重复拒绝）；
四个坐标为 `|v| ≤ 1e9` 的整数，且 `x1 < x2`、`y1 < y2`。

成功 `200`（结果以十进制字符串返回，任意精度）：

```json
{"area": "10", "perimeter": "14"}
```

失败 `400`：带输入位置的稳定错误，**不夹带任何部分结果**：

```json
{
  "error": {
    "code": "invalid_rectangle",
    "message": "rectangles[1]: duplicate id 'x' (id must be unique)",
    "location": {"index": 1, "id": "x"}
  }
}
```

### `GET /healthz`

仅当**请求校验器**、**扫描引擎**与**隔离引擎**三个组件都完成启动自检并
就绪时返回 `200 {"status":"ok", ...}`，否则 `503`。Docker 健康检查即探测此端点。

### `POST /api/separation-audit`

请求体为对象（1–160 个矩形，每个带正整数 `cost`，1 ≤ cost ≤ 1e9）：

```json
{
  "rectangles": [
    {"id": "a", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "cost": 5},
    {"id": "b", "x1": 1, "y1": 0, "x2": 2, "y2": 1, "cost": 2},
    {"id": "c", "x1": 2, "y1": 0, "x2": 3, "y2": 1, "cost": 9}
  ],
  "start": {"x": 0, "y": 0},
  "end": {"x": 2, "y": 0}
}
```

矩形的几何与标识约束同 `/api/audit`（标识唯一、几何重复允许且作为
**可分别清除、分别计价**的来源保留）。`start`/`end` 为整数取样点
（`|x|,|y| ≤ 1e9`，落在边界上算被覆盖）。

连通关系以**闭合矩形的非空交集**建立——共边、共角点都算连通，仅按
面积重叠建图是错的。要切断的是两点在剩余污染区域上的通行：必须把覆盖
某点的所有矩形全部清除才算封住该点。成功 `200`：

```json
{
  "status": "cleaned",
  "cleaned": ["b"],
  "total_cost": "2",
  "reachable_from_start": ["a"]
}
```

* `cleaned` —— 在所有能切断两点的清洗集合中总代价精确最小；
  同代价按**升序框标识列表逐元素字典序最小**裁决；
* `total_cost` —— 最小总代价（十进制字符串，任意精度）；
* `reachable_from_start` —— 清除后从起点仍可达的框标识（升序），
  用于独立复核隔离证据：其中不得出现任何覆盖终点的框。

点未被覆盖、两点原本不连通时给出稳定结论，**不夹带任何部分方案**：

```json
{"status": "point_not_covered", "point": "start"}
{"status": "already_separated", "cleaned": [], "total_cost": "0",
 "reachable_from_start": ["a"]}
```

校验失败仍是带位置的稳定 400（矩形错误定位到 `{index,id}`，点错误
定位到 `{field}`；无部分结果）。

## 隔离裁决算法

**全局精确**带权 s-t 顶点割，非逐次删除、非局部贪心：每个矩形拆成
`v_in → v_out`（容量 = 清洗代价），闭交集的无向边换成两条 ∞ 容量
有向跨接边；超级源连全部覆盖起点的框、覆盖终点的框连超级汇。一次
Dinic 最大流定出全局最小代价。

同代价规范裁决同样一次最大流完成：主容量乘 `B = 2**n`（大于全部
字典序权之和），再给每框 v 加 `source→v_in`、`v_out→sink` 两条
权为 `2**(n-1-rank(v))` 的边（rank 按标识升序）——两权边恰在该框
**未被清洗**时产生割容量，故在代价并列的方案中，最早分歧标识"列入
清洗列表"严格优先，即清洗列表逐元素字典序最小。

## 审计算法

横坐标归并进入/离开事件；纵坐标压缩后用线段树同步维护**覆盖计数、
覆盖总长、连续覆盖段数**（O(n log n)，5 万矩形 < 1s）。同一横坐标的
混合事件整体裁决：进入边在整组应用前的树上、离开边在整组应用后的
树上，查询该边 y 区间内"外侧未覆盖长度"作为外露竖直边——共边与内部
接缝自然计 0，仅角点接触的边也不会被吞掉。面积 = Σ 覆盖总长 × 板宽；
水平边 = Σ 2 × 连续段数 × 板宽。不铺开单位网格、不逐对切割矩形、
不调用几何求解库（纯标准库，零第三方依赖）。隔离裁决的节点拆分
最大流见上节。

## 运行

```bash
# Docker（宿主机端口可配置，默认 8080）
AUDIT_HOST_PORT=9090 docker compose up --build audit

# 一次性验证：跑完即退出，退出码即结果（0 通过 / 1 失败）
docker compose up --build --exit-code-from verify verify

# 本地（无需 Docker，Python ≥ 3.11）
python3 -m app.server                 # AUDIT_PORT 可改端口
python3 -m unittest discover -s tests # 代码测试
python3 verify/run_verifier.py        # 完整验证（需服务已启动）
```

verify 服务依次执行：代码测试（unittest 全量）→ 构建检查（compileall）
→ 等待审计服务健康 → API/HTTP 冒烟（重叠框面积 10 周长 14、相邻方框
不计公共边、重复框不增量、重复标识/非法矩形带位置拒绝且无部分结果；
隔离审计覆盖相切链、角点相切、端点多重覆盖、同代价规范裁决、本不连通
与点未覆盖的稳定结论、非法 cost 拒绝），随后退出并以退出码报告结果。

## 目录

```
app/geometry.py     扫描线引擎 + 共享请求校验（核心，纯标准库）
app/separation.py   隔离审计：闭矩形交集图 + 节点拆分最小割（Dinic）
app/components.py   组件注册表与启动自检（健康检查依据）
app/server.py       HTTP 服务（ThreadingHTTPServer）
tests/              单元 / 随机对照（网格预言机、子集穷举预言机）/ 端到端 HTTP
verify/             一次性验证服务
Dockerfile          审计接口镜像（含 HEALTHCHECK）
docker-compose.yaml audit + verify 编排
```
