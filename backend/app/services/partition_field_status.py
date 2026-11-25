"""分区字段生效状态领域服务：Schema 变更按分区核定字段演进的生效范围登记处。

表查询依据本模块数据识别每个分区实际可用的字段；数据回填写入本模块；
没有回填依据（来源）的分区不得声称新列可用；并发补交同一分区只生效一次；
回填中断后不得把半完成字段列为可用。
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.services.field_version_conversion import record_partition_effective
from app.store import store

MODULE_KEY = "partition_field_status"
EFFECTIVE = "已生效"
PENDING = "待回填"
LEGACY = "旧版本读取"
INTERRUPTED = "已中断"
DEFAULT_BACKFILL_ROWS = 5000

# 提交回填的生效判定必须串行：并发补交同一分区只生效一次。
SUBMIT_LOCK = threading.Lock()


def status_rows(table: Any, field: Any = None, version: Any = None) -> list[dict[str, Any]]:
    """按湖表（可叠加字段/版本）读取分区字段生效状态数据。"""
    rows = [row for row in store.rows(MODULE_KEY) if row.get("目标湖表") == table]
    if field is not None:
        rows = [row for row in rows if row.get("变更字段") == field]
    if version is not None:
        rows = [row for row in rows if str(row.get("目标版本")) == str(version)]
    return rows


def _backfilled_rows(row: dict[str, Any]) -> int:
    """分区回填行数：优先取匹配回填任务的完成行数，否则按默认批次行数登记。"""
    partition = str(row.get("分区") or "")
    for task in store.rows("backfill_pipeline"):
        if task.get("目标湖表") != row.get("目标湖表"):
            continue
        parts = [p for p in re.split(r"[,，、\s]+", str(task.get("回填范围") or "")) if p]
        if partition in parts:
            try:
                return int(task.get("完成行数") or 0)
            except (TypeError, ValueError):
                return DEFAULT_BACKFILL_ROWS
    return DEFAULT_BACKFILL_ROWS


def submit_partition_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, bool, str]:
    """提交分区回填（幂等生效）。

    已生效的重复提交直接忽略；已中断的半完成字段不得生效；
    没有来源（回填依据）的分区保持旧版本读取，不能声称新列可用。
    """
    with SUBMIT_LOCK:
        if row.get("status") == EFFECTIVE:
            return row, True, f"分区 {row.get('分区')} 字段已生效，重复提交忽略"
        if row.get("status") == INTERRUPTED:
            return None, False, f"分区 {row.get('分区')} 回填已中断，需先恢复待回填再提交"
        source = str(row.get("来源") or "").strip()
        if not source:
            row["status"] = LEGACY
            row["pending"] = True
            return None, False, (
                f"缺少来源：{row.get('目标湖表')}/{row.get('分区')} 的 "
                f"{row.get('变更字段')}@{row.get('目标版本')} 回填批次未登记，不能声称新列可用"
            )
        row["status"] = EFFECTIVE
        row["当前读取版本"] = row.get("目标版本")
        row["完成行数"] = _backfilled_rows(row)
        row["生效时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row["pending"] = False
        row["abnormal"] = False
        record_partition_effective(row.get("目标湖表"), row.get("变更字段"), row.get("目标版本"))
        return row, True, f"分区 {row.get('分区')} 字段 {row.get('变更字段')} 已生效"


def apply_backfill_to_partition_status(
    table: Any, partitions: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """数据回填写入分区字段生效状态：返回（已生效列表, 被拦下列表）。"""
    updated: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    wanted = {str(p) for p in partitions if str(p).strip()}
    for row in status_rows(table):
        if str(row.get("分区")) not in wanted:
            continue
        entry, ok, message = submit_partition_row(row)
        item = {"分区": row.get("分区"), "字段": row.get("变更字段"), "message": message}
        if ok:
            item["生效状态"] = entry.get("status") if entry else row.get("status")
            item["完成行数"] = (entry or row).get("完成行数")
            updated.append(item)
        else:
            item["生效状态"] = row.get("status")
            blocked.append(item)
    return updated, blocked


def build_backfill_todo(table: Any, field: Any, version: Any) -> dict[str, Any]:
    """Schema 回填待办：从分区字段生效状态数据汇总，缺来源的分区给出缺失说明。"""
    items: list[dict[str, Any]] = []
    for row in sorted(status_rows(table, field, version), key=lambda r: str(r.get("分区"))):
        source = str(row.get("来源") or "").strip()
        missing = None
        if row.get("status") != EFFECTIVE and not source:
            missing = (
                f"缺少来源：{table}/{row.get('分区')} 的 {field}@{version} "
                f"回填批次未登记，补齐来源后方可提交生效"
            )
        items.append({
            "分区": row.get("分区"),
            "生效状态": row.get("status"),
            "当前读取版本": row.get("当前读取版本"),
            "目标版本": row.get("目标版本"),
            "来源": source or None,
            "完成行数": row.get("完成行数", 0),
            "缺失说明": missing,
        })
    summary = {
        status: sum(1 for item in items if item["生效状态"] == status)
        for status in (EFFECTIVE, PENDING, LEGACY, INTERRUPTED)
    }
    return {
        "items": items,
        "summary": summary,
        "todo_count": len(items) - summary[EFFECTIVE],
    }


class PartitionFieldStatusService(BaseModuleService):
    MODULE = MODULE_KEY
    ENTITY = "分区字段生效状态"
    OPERATION_TERM = "核定"
    LIST_FIELDS = ["状态标识", "目标湖表", "分区", "变更字段", "目标版本", "当前读取版本", "来源", "完成行数"]
    REQUIRED_FIELDS = ["状态标识", "目标湖表", "分区", "变更字段", "目标版本"]
    STATUS_ORDER = [PENDING, LEGACY, EFFECTIVE, INTERRUPTED]
    ACTION_RULES = {"提交回填": EFFECTIVE, "标记中断": INTERRUPTED, "恢复待回填": PENDING}
    NEGATIVE_ACTIONS = ["标记中断"]

    @staticmethod
    def _business_key(values: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(values.get("目标湖表") or ""),
            str(values.get("分区") or ""),
            str(values.get("变更字段") or ""),
            str(values.get("目标版本") or ""),
        )

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记/补交分区字段生效状态。

        同一（湖表, 分区, 字段, 目标版本）只保留一条记录：并发补交合并到既有行，
        带来来源时视为补齐来源并转入待回填；不会重复建行。
        """
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        with SUBMIT_LOCK:
            wanted = self._business_key(values)
            for row in store.rows(self.MODULE):
                if self._business_key(row) != wanted:
                    continue
                source = str(values.get("来源") or "").strip()
                if source and not str(row.get("来源") or "").strip():
                    row["来源"] = source
                    if row.get("status") == LEGACY:
                        row["status"] = PENDING
                return row, []
            entry, missing = super().create_entry(values)
            if entry is not None and not str(entry.get("来源") or "").strip():
                entry["status"] = LEGACY
            return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "提交回填":
            row, ok, message = submit_partition_row(entry)
            return (row if ok else None), message
        if action == "标记中断":
            if entry.get("status") == EFFECTIVE:
                return None, "已生效分区不能标记中断"
            entry["status"] = INTERRUPTED
            entry["pending"] = True
            entry["abnormal"] = True
            return entry, "分区字段已标记中断，半完成字段不再列为可用"
        if action == "恢复待回填":
            if entry.get("status") != INTERRUPTED:
                return None, "仅已中断分区可恢复待回填"
            entry["status"] = PENDING if str(entry.get("来源") or "").strip() else LEGACY
            entry["pending"] = True
            entry["abnormal"] = False
            return entry, "分区字段已恢复待回填"
        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

    def domain_effective_scope(self, entry_id: int) -> dict[str, Any]:
        """生效范围查询：分区字段生效状态数据就是生效范围的登记处。"""
        entry = store.find(self.MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        return {
            "ok": True,
            "status_id": entry.get("状态标识"),
            "table": entry.get("目标湖表"),
            "partition": entry.get("分区"),
            "field": entry.get("变更字段"),
            "target_version": entry.get("目标版本"),
            "read_version": entry.get("当前读取版本"),
            "effective_status": entry.get("status"),
            "source_registered": bool(str(entry.get("来源") or "").strip()),
            "completed_rows": entry.get("完成行数", 0),
            "effective_at": entry.get("生效时间") or None,
        }
