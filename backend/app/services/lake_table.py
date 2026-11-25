"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.partition_field_status import build_backfill_todo, status_rows
from app.store import store


class LakeTableService(BaseModuleService):
    MODULE = "lake_table"
    ENTITY = "湖表定义"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["表标识", "表名称", "主键列", "分区列", "存储格式", "压缩算法", "表版本", "当前快照"]
    REQUIRED_FIELDS = ["表标识", "表名称", "主键列"]
    STATUS_ORDER = ["草稿", "待校验", "已创建", "已冻结"]
    ACTION_RULES = {"提交校验": "待校验", "创建表": "已创建", "冻结表": "已冻结"}
    NEGATIVE_ACTIONS = []

    def domain_define_table(self, entry_id: int) -> dict[str, Any]:
        """对当前数据执行状态，联动状态和处理处理，产出标准化记录。处理流程：读取配置→执行状态→状态校验→处理批次→写入存储，下游可消费且批次可追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回解析处理结果，含计算校验和回填批次信息

            return {"ok": False, "message": f"湖表 {entry_id} 不存在"}        # 返回解析处理结果，含计算校验和回填批次信息

        return {
            "ok": True,
            "table_id": entry.get("表标识"),
            "table_name": entry.get("表名称"),
            "primary_key": entry.get("主键列"),
            "partition_col": entry.get("分区列"),
            "storage_format": entry.get("存储格式", "parquet"),
            "compression": entry.get("压缩算法", "zstd"),
            "ddl": f"CREATE TABLE {entry.get('表标识')} (...) USING iceberg PARTITIONED BY ({entry.get('分区列')})",
            "schema_version": entry.get("表版本", "v1"),
        }

    def domain_partition_fields(self, entry_id: int, version: str | None = None) -> dict[str, Any]:
        """表详情：按分区字段生效状态识别每个分区实际可用的字段。

        只有已生效的分区才暴露新字段；已中断的半完成字段不得列为可用；
        跨分区查询只暴露所有分区都已生效的字段，其余列为字段可见性冲突；
        按旧版本查询时仍返回原字段，原版本读取仍能用。
        查询结果同步 Schema 回填待办（同源数据，刷新即一致）。
        """
        entry = store.find(self.MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": f"湖表 {entry_id} 不存在"}
        table = entry.get("表标识")
        base_fields = [f for f in (entry.get("主键列"), entry.get("分区列")) if f]
        query_version = str(version or entry.get("表版本") or "v1")

        by_partition: dict[str, list[dict[str, Any]]] = {}
        for row in status_rows(table):
            by_partition.setdefault(str(row.get("分区")), []).append(row)

        partitions: list[dict[str, Any]] = []
        for partition, rows in sorted(by_partition.items()):
            available = list(base_fields)
            for row in rows:
                # 仅已生效才把新列列为可用；待回填/旧版本读取/已中断都不暴露
                if str(row.get("目标版本")) == query_version and row.get("status") == "已生效":
                    field = str(row.get("变更字段"))
                    if field not in available:
                        available.append(field)
            effective_row = next(
                (row for row in rows if row.get("status") == "已生效"), None)
            read_version = (
                str(effective_row.get("目标版本")) if effective_row
                else str(rows[0].get("当前读取版本") or "v1")
            )
            if all(row.get("status") == "已生效" for row in rows):
                part_status = "已生效"
            elif any(row.get("status") == "已中断" for row in rows):
                part_status = "已中断"
            elif any(row.get("status") == "待回填" for row in rows):
                part_status = "待回填"
            else:
                part_status = "旧版本读取"
            partitions.append({
                "分区": partition,
                "生效状态": part_status,
                "读取版本": read_version,
                "可用字段": available,
                "完成行数": sum(int(row.get("完成行数") or 0) for row in rows),
            })

        union_fields: list[str] = []
        for part in partitions:
            for field in part["可用字段"]:
                if field not in union_fields:
                    union_fields.append(field)
        if partitions:
            effective_fields = [
                field for field in union_fields
                if all(field in part["可用字段"] for part in partitions)
            ]
        else:
            effective_fields = list(base_fields)
        conflicts = [
            {
                "字段": field,
                "已生效分区": [p["分区"] for p in partitions if field in p["可用字段"]],
                "未生效分区": [p["分区"] for p in partitions if field not in p["可用字段"]],
            }
            for field in union_fields
            if field not in effective_fields
        ]

        # 查询结果同步到 Schema 回填待办：两处都按分区字段生效状态数据出数
        todos: list[dict[str, Any]] = []
        for change in store.rows("stream_schema"):
            if change.get("目标湖表") != table:
                continue
            todo = build_backfill_todo(
                table, change.get("变更字段"), str(change.get("生效版本") or ""))
            if todo["items"]:
                todos.append({
                    "change_id": change.get("变更标识"),
                    "field": change.get("变更字段"),
                    "target_version": str(change.get("生效版本") or ""),
                    **todo,
                })

        return {
            "ok": True,
            "table": table,
            "table_name": entry.get("表名称"),
            "query_version": query_version,
            "base_fields": base_fields,
            "partitions": partitions,
            "effective_fields": effective_fields,
            "conflicts": conflicts,
            "backfill_todo": todos,
        }
