<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="meta.key === 'lake_table'"
              class="link"
              type="button"
              :data-testid="`partition-fields-${row.id}`"
              @click="openPartitionFields(row)"
            >
              分区字段
            </button>
            <button
              v-if="meta.key === 'stream_schema'"
              class="link"
              type="button"
              :data-testid="`backfill-todo-${row.id}`"
              @click="openBackfillTodo(row)"
            >
              回填待办
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="detailKind" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal detail-modal">
        <template v-if="detailKind === 'partition-fields'">
          <h3>表详情：{{ partitionFields?.table_name }}（{{ partitionFields?.table }}）分区字段</h3>
          <div class="detail-toolbar">
            <label class="detail-version">
              <span>查询版本</span>
              <select v-model="detailVersion" data-testid="detail-version" @change="refreshDetail">
                <option v-for="option in versionOptions" :key="option" :value="option">{{ option }}</option>
              </select>
            </label>
            <button class="btn" data-testid="detail-refresh" type="button" @click="refreshDetail">刷新</button>
          </div>
          <template v-if="partitionFields">
            <p class="detail-line">基础字段：{{ partitionFields.base_fields.join('、') || '—' }}</p>
            <p class="detail-line">
              跨分区实际可用字段：{{ partitionFields.effective_fields.join('、') || '—' }}
            </p>
            <table class="data-table">
              <thead>
                <tr><th>分区</th><th>生效状态</th><th>读取版本</th><th>可用字段</th><th>完成行数</th></tr>
              </thead>
              <tbody>
                <tr v-for="part in partitionFields.partitions" :key="part.分区" :data-testid="`detail-partition-${part.分区}`">
                  <td>{{ part.分区 }}</td>
                  <td>{{ part.生效状态 }}</td>
                  <td>{{ part.读取版本 }}</td>
                  <td>{{ part.可用字段.join('、') }}</td>
                  <td>{{ part.完成行数 }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="partitionFields.conflicts.length" class="detail-block">
              <h4>字段可见性冲突</h4>
              <p v-for="conflict in partitionFields.conflicts" :key="conflict.字段" class="detail-line">
                {{ conflict.字段 }}：已生效分区 {{ conflict.已生效分区.join('、') || '无' }}；
                未生效分区 {{ conflict.未生效分区.join('、') || '无' }}
              </p>
            </div>
            <div v-for="todo in partitionFields.backfill_todo" :key="todo.change_id" class="detail-block">
              <h4>Schema 回填待办（{{ todo.change_id }}：{{ todo.field }}@{{ todo.target_version }}）</h4>
              <p class="detail-line">
                已生效 {{ todo.summary['已生效'] ?? 0 }} / 待回填 {{ todo.summary['待回填'] ?? 0 }} /
                旧版本读取 {{ todo.summary['旧版本读取'] ?? 0 }} / 已中断 {{ todo.summary['已中断'] ?? 0 }}
              </p>
            </div>
          </template>
        </template>

        <template v-if="detailKind === 'backfill-todo'">
          <h3>Schema 回填待办：{{ backfillTodo?.change_id }}（{{ backfillTodo?.field }}@{{ backfillTodo?.target_version }}）</h3>
          <div class="detail-toolbar">
            <button class="btn" data-testid="todo-refresh" type="button" @click="refreshDetail">刷新</button>
          </div>
          <template v-if="backfillTodo">
            <p class="detail-line">
              已生效 {{ backfillTodo.summary['已生效'] ?? 0 }} / 待回填 {{ backfillTodo.summary['待回填'] ?? 0 }} /
              旧版本读取 {{ backfillTodo.summary['旧版本读取'] ?? 0 }} / 已中断 {{ backfillTodo.summary['已中断'] ?? 0 }}；
              待办 {{ backfillTodo.todo_count }} 项
            </p>
            <table class="data-table">
              <thead>
                <tr><th>分区</th><th>生效状态</th><th>当前读取版本</th><th>目标版本</th><th>来源</th><th>完成行数</th><th>缺失说明</th></tr>
              </thead>
              <tbody>
                <tr v-for="item in backfillTodo.items" :key="item.分区" :data-testid="`todo-partition-${item.分区}`">
                  <td>{{ item.分区 }}</td>
                  <td>{{ item.生效状态 }}</td>
                  <td>{{ item.当前读取版本 }}</td>
                  <td>{{ item.目标版本 }}</td>
                  <td>{{ item.来源 ?? '—' }}</td>
                  <td>{{ item.完成行数 }}</td>
                  <td>{{ item.缺失说明 ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </template>

        <p v-if="detailError" class="error-text">{{ detailError }}</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="closeDetail">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listEntries,
  loadBackfillTodo,
  loadPartitionFields,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { BackfillTodoResult, EntryRow, PartitionFieldsResult } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const detailKind = ref<'' | 'partition-fields' | 'backfill-todo'>('')
const detailRow = ref<EntryRow | null>(null)
const partitionFields = ref<PartitionFieldsResult | null>(null)
const backfillTodo = ref<BackfillTodoResult | null>(null)
const detailVersion = ref('')
const detailError = ref('')
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)
const versionOptions = computed(() => {
  const options = new Set<string>(['v1'])
  if (detailRow.value?.表版本) options.add(String(detailRow.value.表版本))
  if (partitionFields.value?.query_version) options.add(partitionFields.value.query_version)
  return [...options]
})

async function openPartitionFields(row: EntryRow) {
  detailRow.value = row
  detailKind.value = 'partition-fields'
  detailVersion.value = String(row.表版本 || 'v1')
  partitionFields.value = null
  await refreshDetail()
}

async function openBackfillTodo(row: EntryRow) {
  detailRow.value = row
  detailKind.value = 'backfill-todo'
  backfillTodo.value = null
  await refreshDetail()
}

async function refreshDetail() {
  detailError.value = ''
  if (!detailRow.value) return
  try {
    if (detailKind.value === 'partition-fields') {
      const result = await loadPartitionFields(Number(detailRow.value.id), detailVersion.value)
      if (!result.ok) throw new Error(result.message || '表详情读取失败')
      partitionFields.value = result
    } else if (detailKind.value === 'backfill-todo') {
      const result = await loadBackfillTodo(Number(detailRow.value.id))
      if (!result.ok) throw new Error(result.message || '回填待办读取失败')
      backfillTodo.value = result
    }
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '详情读取失败'
  }
}

function closeDetail() {
  detailKind.value = ''
  detailRow.value = null
  partitionFields.value = null
  backfillTodo.value = null
  detailError.value = ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
