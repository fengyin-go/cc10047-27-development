import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  BackfillTodoResult,
  EntryRow,
  OverviewResult,
  PageResult,
  PartitionFieldsResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// 表详情：按分区字段生效状态识别每个分区实际可用的字段
export async function loadPartitionFields(
  tableId: number,
  version?: string,
): Promise<PartitionFieldsResult> {
  const query = version ? `?version=${encodeURIComponent(version)}` : ''
  const response = await request(`/api/lake_table/${tableId}/partition-fields${query}`)
  return await response.json()
}

// Schema 回填待办：每个分区的字段生效状态与缺来源说明
export async function loadBackfillTodo(changeId: number): Promise<BackfillTodoResult> {
  const response = await request(`/api/stream_schema/${changeId}/backfill-todo`)
  return await response.json()
}
