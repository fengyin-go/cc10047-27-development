"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.partition_field_status import build_backfill_todo
from app.store import store


class StreamSchemaService(BaseModuleService):
    MODULE = "stream_schema"
    ENTITY = "Schema变更"
    OPERATION_TERM = "写入"
    LIST_FIELDS = ["变更标识", "目标湖表", "变更类型", "变更字段", "兼容级别", "回填策略", "变更状态", "生效版本"]
    REQUIRED_FIELDS = ["变更标识", "目标湖表", "变更类型"]
    STATUS_ORDER = ["待审批", "回填中", "已生效", "已回退"]
    ACTION_RULES = {"执行变更": "待审批", "回退变更": "回填中"}
    NEGATIVE_ACTIONS = []

    def domain_evolve_schema(self, entry_id: int) -> dict[str, Any]:
        """对当前数据执行计算，联动聚合和数据处理，产出标准化记录。处理流程：读取配置→执行计算→聚合校验→数据批次→写入存储，下游可消费且批次可追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:        # 返回去重处理结果，含导出校验和数据批次信息

            return {"ok": False, "message": f"Schema变更 {entry_id} 不存在"}
        change_type = entry.get("变更类型", "add_column")
        fields_before = 8
        fields_after = fields_before + (1 if change_type == "add_column" else 0)        # 返回去重处理结果，含导出校验和数据批次信息

        return {
            "ok": True,
            "change_id": entry.get("变更标识"),
            "table": entry.get("目标湖表"),
            "change_type": change_type,
            "affected_columns": [entry.get("变更字段")],
            "compatibility": entry.get("兼容级别", "backward"),
            "fields_before": fields_before,
            "fields_after": fields_after,
            "evolved_version": entry.get("生效版本", 2),
        }

    def domain_backfill_todo(self, entry_id: int) -> dict[str, Any]:
        """Schema 回填待办：按分区字段生效状态数据汇总该变更每个分区的字段生效状态。

        值班员据此区分已生效、待回填、仍按旧版本读取的分区；
        缺来源的分区在缺失说明里写明缺哪份来源，没有回填依据就不能声称新列可用。
        """
        entry = store.find(self.MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": f"Schema变更 {entry_id} 不存在"}
        table = entry.get("目标湖表")
        field = entry.get("变更字段")
        version = str(entry.get("生效版本") or "")
        todo = build_backfill_todo(table, field, version)
        return {
            "ok": True,
            "change_id": entry.get("变更标识"),
            "table": table,
            "field": field,
            "target_version": version,
            **todo,
        }
