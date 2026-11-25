"""分区字段生效状态：Schema 变更按分区核定字段演进的端到端验证。

场景：orders 增加 tax_code（v2）后，p1 已准备好 v2、p2 仍缺来源；
跨分区查询字段可见性冲突，回填待办说明 p2 缺少哪份来源；
补齐 p2 后重复提交 v2 只生效一次，刷新表详情与待办核对字段、版本和行数。
"""
from __future__ import annotations

import sys
sys.path.insert(0, '.')

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app
from app.services import service_for
from app.store import store

client = TestClient(app)

ORDERS_TABLE_ID = 4      # lake_table 中 orders 的种子 id
ORDERS_CHANGE_ID = 4     # stream_schema 中 orders+tax_code 的种子 id
ORDERS_BACKFILL_ID = 4   # backfill_pipeline 中 BF-orders-p1 的种子 id
P1_STATUS_ID = 1         # partition_field_status 中 orders/p1 的种子 id
P2_STATUS_ID = 2         # partition_field_status 中 orders/p2 的种子 id


def _pfs():
    return service_for("partition_field_status")


def _orders_row(status_id):
    return store.find("partition_field_status", status_id)


class TestSeedScenario:
    def test_orders_seed_ready(self):
        table = store.find("lake_table", ORDERS_TABLE_ID)
        assert table["表标识"] == "orders"
        change = store.find("stream_schema", ORDERS_CHANGE_ID)
        assert change["变更字段"] == "tax_code"
        assert change["生效版本"] == "v2"
        p1 = _orders_row(P1_STATUS_ID)
        p2 = _orders_row(P2_STATUS_ID)
        assert p1["status"] == "待回填" and p1["来源"]
        assert p2["status"] == "旧版本读取" and not p2["来源"]


class TestInitialVisibility:
    def test_cross_partition_query_before_backfill(self):
        svc = service_for("lake_table")
        result = svc.domain_partition_fields(ORDERS_TABLE_ID, version="v2")
        assert result["ok"] is True
        assert result["base_fields"] == ["order_id", "dt"]
        # 没有回填依据就不能声称新列可用：p1 待回填、p2 旧版本读取，tax_code 均不可用
        for part in result["partitions"]:
            assert "tax_code" not in part["可用字段"]
        assert result["effective_fields"] == ["order_id", "dt"]
        assert result["conflicts"] == []

    def test_todo_explains_missing_source(self):
        svc = service_for("stream_schema")
        todo = svc.domain_backfill_todo(ORDERS_CHANGE_ID)
        assert todo["ok"] is True
        assert todo["summary"]["待回填"] == 1
        assert todo["summary"]["旧版本读取"] == 1
        items = {item["分区"]: item for item in todo["items"]}
        assert items["p1"]["缺失说明"] is None
        assert "缺少来源" in items["p2"]["缺失说明"]
        assert "p2" in items["p2"]["缺失说明"]

    def test_old_version_read_still_works(self):
        svc = service_for("lake_table")
        result = svc.domain_partition_fields(ORDERS_TABLE_ID, version="v1")
        assert result["ok"] is True
        assert result["effective_fields"] == ["order_id", "dt"]
        assert result["conflicts"] == []
        for part in result["partitions"]:
            assert part["可用字段"] == ["order_id", "dt"]


class TestBackfillWritesStatus:
    def test_backfill_makes_p1_effective(self):
        svc = service_for("backfill_pipeline")
        result = svc.domain_backfill(ORDERS_BACKFILL_ID)
        assert result["ok"] is True
        assert [item["分区"] for item in result["partition_field_status"]] == ["p1"]
        p1 = _orders_row(P1_STATUS_ID)
        assert p1["status"] == "已生效"
        assert p1["当前读取版本"] == "v2"
        assert p1["完成行数"] == 12000
        p2 = _orders_row(P2_STATUS_ID)
        assert p2["status"] == "旧版本读取"

    def test_field_visibility_conflict_after_p1(self):
        svc = service_for("lake_table")
        result = svc.domain_partition_fields(ORDERS_TABLE_ID, version="v2")
        parts = {p["分区"]: p for p in result["partitions"]}
        assert "tax_code" in parts["p1"]["可用字段"]
        assert parts["p1"]["读取版本"] == "v2"
        assert parts["p1"]["完成行数"] == 12000
        assert "tax_code" not in parts["p2"]["可用字段"]
        assert parts["p2"]["读取版本"] == "v1"
        # 跨两个分区查询时字段可见性冲突：tax_code 只在 p1 生效
        assert result["effective_fields"] == ["order_id", "dt"]
        assert result["conflicts"] == [
            {"字段": "tax_code", "已生效分区": ["p1"], "未生效分区": ["p2"]}
        ]
        # 查询结果同步到 Schema 回填待办
        todo = result["backfill_todo"][0]
        assert todo["summary"]["已生效"] == 1
        items = {item["分区"]: item for item in todo["items"]}
        assert "缺少来源" in items["p2"]["缺失说明"]

    def test_submit_without_source_rejected(self):
        entry, message = _pfs().run_action(P2_STATUS_ID, "提交回填")
        assert entry is None
        assert "缺少来源" in message
        p2 = _orders_row(P2_STATUS_ID)
        assert p2["status"] == "旧版本读取"
        assert p2["当前读取版本"] == "v1"


class TestSourceCompletionAndDuplicateSubmit:
    def test_merge_create_fills_source(self):
        entry, missing = _pfs().create_entry({
            "状态标识": "PFS-orders-p2-tax_code",
            "目标湖表": "orders",
            "分区": "p2",
            "变更字段": "tax_code",
            "目标版本": "v2",
            "当前读取版本": "v1",
            "来源": "kafka://orders-backfill/p2",
            "完成行数": 0,
        })
        assert missing == []
        assert entry["id"] == P2_STATUS_ID  # 同键合并，不重复建行
        assert entry["status"] == "待回填"
        assert entry["来源"] == "kafka://orders-backfill/p2"
        rows = [r for r in store.rows("partition_field_status")
                if r.get("目标湖表") == "orders" and r.get("分区") == "p2"]
        assert len(rows) == 1

    def test_submit_p2_then_duplicate_ignored(self):
        entry, message = _pfs().run_action(P2_STATUS_ID, "提交回填")
        assert entry is not None and entry["status"] == "已生效"
        assert entry["当前读取版本"] == "v2"
        assert entry["完成行数"] == 5000  # p2 无回填任务，按默认批次行数登记
        # 重复提交 v2：只生效一次，行数不重复累计
        again, message2 = _pfs().run_action(P2_STATUS_ID, "提交回填")
        assert again is not None
        assert "重复提交" in message2
        assert again["完成行数"] == 5000
        conv = store.rows("field_version_conversion")[0]
        assert conv["覆盖分区"].split(",").count("p2") == 1

    def test_refresh_both_views(self):
        svc = service_for("lake_table")
        result = svc.domain_partition_fields(ORDERS_TABLE_ID, version="v2")
        parts = {p["分区"]: p for p in result["partitions"]}
        for name in ("p1", "p2"):
            assert parts[name]["生效状态"] == "已生效"
            assert parts[name]["读取版本"] == "v2"
            assert "tax_code" in parts[name]["可用字段"]
        assert parts["p1"]["完成行数"] == 12000
        assert parts["p2"]["完成行数"] == 5000
        assert result["effective_fields"] == ["order_id", "dt", "tax_code"]
        assert result["conflicts"] == []
        todo = service_for("stream_schema").domain_backfill_todo(ORDERS_CHANGE_ID)
        assert todo["summary"]["已生效"] == 2
        assert todo["todo_count"] == 0
        conv = service_for("field_version_conversion").domain_conversion_scope(1)
        assert conv["status"] == "已生效"
        assert conv["effective_partitions"] == ["p1", "p2"]
        assert conv["registered_in"] == "partition_field_status"


class TestConcurrency:
    def test_concurrent_submit_takes_effect_once(self):
        entry, _ = _pfs().create_entry({
            "状态标识": "PFS-stress-px-f1",
            "目标湖表": "stress_tbl",
            "分区": "px",
            "变更字段": "f1",
            "目标版本": "v9",
            "当前读取版本": "v1",
            "来源": "s3://stress/px",
            "完成行数": 0,
        })
        with ThreadPoolExecutor(max_workers=8) as pool:
            messages = list(pool.map(
                lambda _: _pfs().run_action(entry["id"], "提交回填")[1], range(16)))
        transitions = [m for m in messages if "重复提交" not in m]
        assert len(transitions) == 1  # 并发补交同一分区只生效一次
        row = store.find("partition_field_status", entry["id"])
        assert row["status"] == "已生效"
        assert row["完成行数"] == 5000
        conv = [c for c in store.rows("field_version_conversion")
                if c.get("目标湖表") == "stress_tbl"]
        assert len(conv) == 1
        assert conv[0]["覆盖分区"] == "px"

    def test_concurrent_create_merges_same_key(self):
        values = {
            "状态标识": "PFS-stress2-py-f2",
            "目标湖表": "stress_tbl2",
            "分区": "py",
            "变更字段": "f2",
            "目标版本": "v3",
            "当前读取版本": "v1",
            "来源": "s3://stress/py",
            "完成行数": 0,
        }
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: _pfs().create_entry(dict(values))[0]["id"], range(16)))
        assert len(set(ids)) == 1
        rows = [r for r in store.rows("partition_field_status")
                if r.get("状态标识") == "PFS-stress2-py-f2"]
        assert len(rows) == 1


class TestInterruptedBackfill:
    def test_interrupted_partition_not_listed_as_available(self):
        svc = _pfs()
        entry, _ = svc.create_entry({
            "状态标识": "PFS-legacy-pz-f3",
            "目标湖表": "legacy_tbl",
            "分区": "pz",
            "变更字段": "f3",
            "目标版本": "v2",
            "当前读取版本": "v1",
            "来源": "s3://legacy/pz",
            "完成行数": 0,
        })
        status_id = entry["id"]
        entry, message = svc.run_action(status_id, "标记中断")
        assert entry["status"] == "已中断"
        assert entry["abnormal"] is True
        # 回填中断后不得把半完成字段列为可用
        table = service_for("lake_table").create_entry({
            "表标识": "legacy_tbl", "表名称": "遗留表", "主键列": "id", "分区列": "dt",
            "存储格式": "parquet", "压缩算法": "zstd", "表版本": "v2", "当前快照": "s0",
        })[0]
        detail = service_for("lake_table").domain_partition_fields(table["id"], version="v2")
        part = detail["partitions"][0]
        assert part["生效状态"] == "已中断"
        assert "f3" not in part["可用字段"]
        # 中断分区不能直接提交生效，需先恢复待回填
        entry, message = svc.run_action(status_id, "提交回填")
        assert entry is None and "中断" in message
        entry, _ = svc.run_action(status_id, "恢复待回填")
        assert entry["status"] == "待回填"
        entry, _ = svc.run_action(status_id, "提交回填")
        assert entry["status"] == "已生效"
        detail = service_for("lake_table").domain_partition_fields(table["id"], version="v2")
        assert "f3" in detail["partitions"][0]["可用字段"]


class TestEndpoints:
    def test_partition_fields_endpoint(self):
        resp = client.get(f"/api/lake_table/{ORDERS_TABLE_ID}/partition-fields", params={"version": "v2"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["table"] == "orders"
        assert {p["分区"] for p in data["partitions"]} == {"p1", "p2"}
        assert "backfill_todo" in data

    def test_backfill_todo_endpoint(self):
        resp = client.get(f"/api/stream_schema/{ORDERS_CHANGE_ID}/backfill-todo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["field"] == "tax_code"
        assert data["target_version"] == "v2"
        assert len(data["items"]) == 2

    def test_partition_field_status_resource_endpoints(self):
        resp = client.get("/api/partition_field_status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        resp = client.post(
            f"/api/partition_field_status/{P1_STATUS_ID}/actions",
            json={"action": "提交回填"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True  # 已生效，重复提交忽略

    def test_field_version_conversion_resource_endpoint(self):
        resp = client.get("/api/field_version_conversion")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_partition_fields_unknown_table_404(self):
        resp = client.get("/api/lake_table/99999/partition-fields")
        assert resp.status_code == 404

    def test_backfill_todo_unknown_change_404(self):
        resp = client.get("/api/stream_schema/99999/backfill-todo")
        assert resp.status_code == 404
