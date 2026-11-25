"""Schema 演进按分区核定接口：表详情分区字段可见性与 Schema 回填待办。

表查询依据分区字段生效状态数据识别每个分区实际可用的字段；
查询结果与 Schema 回填待办同源，刷新两处看到的状态一致。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services import service_for

router = APIRouter(prefix="/api", tags=["Schema分区演进"])


@router.get("/lake_table/{entry_id}/partition-fields")
def partition_fields(entry_id: int, version: str | None = Query(default=None)) -> dict:
    """表详情：每个分区实际可用的字段、读取版本、行数和跨分区字段可见性冲突。"""
    service = service_for("lake_table")
    if service is None:
        raise HTTPException(404, "lake_table 服务未注册")
    result = service.domain_partition_fields(entry_id, version=version)
    if not result.get("ok"):
        raise HTTPException(404, result.get("message"))
    return result


@router.get("/stream_schema/{entry_id}/backfill-todo")
def backfill_todo(entry_id: int) -> dict:
    """Schema 回填待办：按分区列出字段生效状态，缺来源的分区给出缺失说明。"""
    service = service_for("stream_schema")
    if service is None:
        raise HTTPException(404, "stream_schema 服务未注册")
    result = service.domain_backfill_todo(entry_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("message"))
    return result
