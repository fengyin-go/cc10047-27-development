"""字段版本转换领域服务：登记字段在表版本间的转换规则。

转换的覆盖分区不在这里拍脑袋写死，而是从分区字段生效状态数据核算：
生效范围登记在分区字段生效状态数据，本模块只按它汇总转换进度。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

MODULE_KEY = "field_version_conversion"


def _key(table: Any, field: Any, target_version: Any) -> tuple[str, str, str]:
    return (str(table), str(field), str(target_version))


def effective_partitions(table: Any, field: Any, target_version: Any) -> tuple[list[str], list[str]]:
    """从分区字段生效状态数据核算（已生效分区, 全部分区）。"""
    wanted = _key(table, field, target_version)
    scoped = [
        row for row in store.rows("partition_field_status")
        if _key(row.get("目标湖表"), row.get("变更字段"), row.get("目标版本")) == wanted
    ]
    effective = sorted({str(row.get("分区")) for row in scoped if row.get("status") == "已生效"})
    total = sorted({str(row.get("分区")) for row in scoped})
    return effective, total


def record_partition_effective(table: Any, field: Any, target_version: Any) -> dict[str, Any]:
    """分区字段生效后登记字段版本转换。

    覆盖分区从分区字段生效状态数据核算，同一分区重复生效只登记一次；
    全部相关分区都已生效时转换才标记为已生效。
    """
    rows = store.rows(MODULE_KEY)
    wanted = _key(table, field, target_version)
    conv = next(
        (row for row in rows
         if _key(row.get("目标湖表"), row.get("变更字段"), row.get("目标版本")) == wanted),
        None,
    )
    if conv is None:
        conv = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": "已登记",
            "pending": True,
            "abnormal": False,
            "转换标识": f"FVC-{table}-{field}-v1-{target_version}",
            "目标湖表": table,
            "变更字段": field,
            "源版本": "v1",
            "目标版本": target_version,
            "转换方式": "add_column 默认值补0",
            "覆盖分区": "",
            "登记时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows.append(conv)
    effective, total = effective_partitions(table, field, target_version)
    conv["覆盖分区"] = ",".join(effective)
    if effective and effective == total:
        conv["status"] = "已生效"
    elif effective:
        conv["status"] = "部分生效"
    else:
        conv["status"] = "已登记"
    conv["pending"] = conv["status"] not in ("已生效", "已作废")
    return conv


class FieldVersionConversionService(BaseModuleService):
    MODULE = MODULE_KEY
    ENTITY = "字段版本转换"
    OPERATION_TERM = "登记"
    LIST_FIELDS = ["转换标识", "目标湖表", "变更字段", "源版本", "目标版本", "转换方式", "覆盖分区", "登记时间"]
    REQUIRED_FIELDS = ["转换标识", "目标湖表", "变更字段", "目标版本"]
    STATUS_ORDER = ["已登记", "部分生效", "已生效", "已作废"]
    ACTION_RULES = {"核算生效范围": "已登记", "作废转换": "已作废"}
    NEGATIVE_ACTIONS = ["作废转换"]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """同一（湖表, 字段, 目标版本）只保留一条转换记录，重复登记合并而非重复建行。"""
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        wanted = _key(values.get("目标湖表"), values.get("变更字段"), values.get("目标版本"))
        for row in store.rows(self.MODULE):
            if _key(row.get("目标湖表"), row.get("变更字段"), row.get("目标版本")) == wanted:
                for field in ("源版本", "转换方式"):
                    if str(values.get(field) or "").strip():
                        row[field] = values.get(field)
                return row, []
        return super().create_entry(values)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "核算生效范围":
            record_partition_effective(entry.get("目标湖表"), entry.get("变更字段"), entry.get("目标版本"))
            return entry, f"{self.ENTITY}已按分区字段生效状态核算覆盖分区"
        if action == "作废转换":
            entry["status"] = "已作废"
            entry["pending"] = False
            entry["abnormal"] = True
            return entry, f"{self.ENTITY}已作废"
        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

    def domain_conversion_scope(self, entry_id: int) -> dict[str, Any]:
        """输出转换的生效范围：以分区字段生效状态数据为准。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": f"字段版本转换 {entry_id} 不存在"}
        effective, total = effective_partitions(
            entry.get("目标湖表"), entry.get("变更字段"), entry.get("目标版本"))
        return {
            "ok": True,
            "conversion_id": entry.get("转换标识"),
            "table": entry.get("目标湖表"),
            "field": entry.get("变更字段"),
            "from_version": entry.get("源版本"),
            "to_version": entry.get("目标版本"),
            "status": entry.get("status"),
            "effective_partitions": effective,
            "pending_partitions": [p for p in total if p not in effective],
            "registered_in": "partition_field_status",
        }
