/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

// 表详情：按分区字段生效状态识别的每个分区实际可用字段
export type PartitionFieldItem = {
  分区: string
  生效状态: string
  读取版本: string
  可用字段: string[]
  完成行数: number
}

export type FieldConflict = {
  字段: string
  已生效分区: string[]
  未生效分区: string[]
}

export type BackfillTodoItem = {
  分区: string
  生效状态: string
  当前读取版本: string
  目标版本: string
  来源: string | null
  完成行数: number
  缺失说明: string | null
}

export type BackfillTodoResult = {
  ok: boolean
  change_id: string
  table: string
  field: string
  target_version: string
  summary: Record<string, number>
  todo_count: number
  items: BackfillTodoItem[]
  message?: string
}

export type PartitionFieldsResult = {
  ok: boolean
  table: string
  table_name: string
  query_version: string
  base_fields: string[]
  partitions: PartitionFieldItem[]
  effective_fields: string[]
  conflicts: FieldConflict[]
  backfill_todo: BackfillTodoResult[]
  message?: string
}
