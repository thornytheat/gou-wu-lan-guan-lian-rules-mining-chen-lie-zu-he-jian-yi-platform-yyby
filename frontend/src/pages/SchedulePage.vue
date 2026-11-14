<template>
  <div class="schedule">
    <PageHeader
      eyebrow="04 · 发布前排期核对"
      title="货架位排期"
      description="按生效 / 结束日期占用货架位：端架与堆头同一时段只承接一个活动。可用位、跨建议单冲突组合、发布后的最终结果都在这一页核对。"
    >
      <template #actions>
        <label class="win">
          <span class="win__label">窗口从</span>
          <input v-model="winFrom" class="field" type="date" data-test="win-from" />
        </label>
        <label class="win">
          <span class="win__label">到</span>
          <input v-model="winTo" class="field" type="date" data-test="win-to" />
        </label>
        <button class="btn" type="button" data-test="reload-overview" :disabled="working" @click="reload">
          刷新排期
        </button>
      </template>
    </PageHeader>

    <div v-if="!canWrite" class="notice notice--tag">{{ MESSAGES.viewerScheduleHint }}</div>
    <div v-if="store.error" class="notice notice--alert">{{ store.error }}</div>
    <div v-else-if="store.notice" class="notice notice--mint" data-test="schedule-notice">{{ store.notice }}</div>

    <template v-if="overview">
      <div class="strip">
        <StatSlip :items="stats" />
      </div>

      <ShelfBand
        index="01"
        title="货架位占用总览"
        :caption="`窗口 ${overview.window.from} → ${overview.window.to}；同一货架位同一时段只承接一个活动`"
      >
        <div class="positions">
          <article
            v-for="pos in overview.positions"
            :key="pos.conflict_group"
            class="position"
            :class="`position--${pos.state}`"
          >
            <div class="position__marker">
              <span class="position__group">{{ pos.conflict_group }}</span>
              <span class="position__zone">{{ pos.zone }}</span>
              <span :class="['chip', stateChip(pos.state)]">{{ STATE_LABELS[pos.state] }}</span>
            </div>
            <div class="position__body">
              <span v-if="!pos.occupancies.length" class="position__free">窗口内可排</span>
              <div v-for="occ in pos.occupancies" :key="occ.schedule_key || occ.slot_key" class="occ">
                <span class="occ__plan tag-num">{{ occ.plan_code }}</span>
                <span class="occ__rule">{{ occ.rule_label || occ.shelf_label }}</span>
                <span class="occ__dates tag-num">{{ occ.effective_date }} → {{ occ.end_date }}</span>
                <span :class="['chip', phaseChip(occ.phase)]">{{ SCHEDULE_PHASE_LABELS[occ.phase] }}</span>
              </div>
            </div>
          </article>
        </div>
      </ShelfBand>

      <ShelfBand
        index="02"
        title="冲突组合"
        :caption="overview.conflicts.length ? '同一货架位被多个建议单占用同一时段，发布前必须先消解' : '当前没有跨建议单冲突'"
      >
        <div v-if="!overview.conflicts.length" class="muted">所有有效占用互不重叠，可以直接发布。</div>
        <div v-else class="conflicts">
          <article v-for="item in overview.conflicts" :key="item.conflict_group + item.plans.join()" class="conflict">
            <div class="conflict__head">
              <span class="chip chip--alert">冲突</span>
              <span class="conflict__group">{{ item.conflict_group }} · {{ item.zone }}</span>
              <span class="conflict__window tag-num">重叠 {{ item.overlap_start }} → {{ item.overlap_end }}</span>
            </div>
            <div class="conflict__plans">
              <span v-for="plan in item.plans" :key="plan" class="chip chip--tag">{{ plan }}</span>
            </div>
            <ul class="conflict__rows">
              <li v-for="row in item.rows" :key="row.schedule_key || row.slot_key">
                <span class="tag-num">{{ row.plan_code }}</span>
                <span>{{ row.rule_label || row.shelf_label }}</span>
                <span class="tag-num">{{ row.effective_date }} → {{ row.end_date }}</span>
                <span :class="['chip', phaseChip(row.phase)]">{{ SCHEDULE_PHASE_LABELS[row.phase] }}</span>
              </li>
            </ul>
          </article>
        </div>
      </ShelfBand>

      <ShelfBand
        v-if="canWrite"
        index="03"
        title="发布核对"
        caption="预览不落库；应用 = 发布建议单并原子切换排期，失败保留旧版"
      >
        <div class="checker">
          <div class="checker__form">
            <label class="checker__field">
              <span class="win__label">建议单</span>
              <select v-model="checkPlan" class="field" data-test="check-plan">
                <option v-for="item in plans" :key="item.plan_code" :value="item.plan_code">
                  {{ item.plan_code }} · {{ item.slot_count }} 位 · {{ SLOT_STATUS_LABELS[item.status] }}
                </option>
              </select>
            </label>
            <label class="checker__field">
              <span class="win__label">生效日期</span>
              <input v-model="effDate" class="field" type="date" data-test="check-eff" />
            </label>
            <label class="checker__field">
              <span class="win__label">结束日期</span>
              <input v-model="endDate" class="field" type="date" data-test="check-end" />
            </label>
            <div class="checker__actions">
              <button
                class="btn"
                type="button"
                data-test="run-preview"
                :disabled="!checkPlan || working"
                @click="runPreview"
              >
                预览核对
              </button>
              <button
                v-if="canPublish"
                class="btn btn--mint"
                type="button"
                data-test="apply-schedule"
                :disabled="!preview || !preview.ok || working"
                :title="preview && !preview.ok ? '有冲突未消解，不能应用' : ''"
                @click="apply"
              >
                应用并发布
              </button>
            </div>
          </div>

          <template v-if="preview">
            <div v-if="!preview.ok" class="notice notice--alert" data-test="preview-conflict">
              {{ preview.conflicts.length }} 组冲突未消解：
              {{ preview.conflicts.map((c) => `${c.conflict_group}（${c.overlap_start} → ${c.overlap_end}）`).join('、') }}
              —— 调整生效 / 结束日期后重新预览。
            </div>
            <div v-else class="notice notice--mint" data-test="preview-ok">
              窗口 {{ preview.window.effective_date }} → {{ preview.window.end_date }} 不与其他建议单冲突，可以发布。
            </div>

            <div class="counts">
              <span class="chip">保持原样 {{ preview.counts.keep }}</span>
              <span class="chip">沿用 {{ preview.counts.unchanged }}</span>
              <span class="chip chip--tag">将替换 {{ preview.counts.replaced }}</span>
              <span class="chip chip--mint">新增 {{ preview.counts.created }}</span>
              <span v-if="preview.counts.released" class="chip chip--alert">释放 {{ preview.counts.released }}</span>
            </div>

            <div v-if="preview.replace.length" class="replace-list">
              <div class="replace-list__title">待生效占用将被替换（已执行部分保持原样）</div>
              <div v-for="item in preview.replace" :key="item.old.slot_key" class="replace">
                <span class="replace__rule">{{ item.new.rule_label || item.new.shelf_label }}</span>
                <span class="replace__old tag-num">{{ item.old.effective_date }} → {{ item.old.end_date }}</span>
                <span class="replace__arrow">⇒</span>
                <span class="replace__new tag-num">{{ item.new.effective_date }} → {{ item.new.end_date }}</span>
              </div>
            </div>

            <table class="grid-table final" data-test="preview-final">
              <thead>
                <tr>
                  <th>货架位</th>
                  <th>组合</th>
                  <th>生效 → 结束</th>
                  <th>阶段</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in preview.final" :key="row.slot_key">
                  <td>
                    <span class="final__group">{{ row.conflict_group }}</span>
                    <span class="final__shelf">{{ row.shelf_label }}</span>
                  </td>
                  <td>{{ row.rule_label || '—' }}</td>
                  <td class="tag-num">{{ row.effective_date }} → {{ row.end_date }}</td>
                  <td><span :class="['chip', phaseChip(row.phase)]">{{ SCHEDULE_PHASE_LABELS[row.phase] }}</span></td>
                </tr>
              </tbody>
            </table>
          </template>
          <div v-else class="muted">选好建议单与档期后点「预览核对」，这里会列出保持原样 / 替换 / 新增的占用与最终结果。</div>
        </div>
      </ShelfBand>
    </template>

    <EmptyState v-else title="排期加载失败" :hint="store.error || MESSAGES.offline" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PageHeader from '../components/common/PageHeader.vue'
import ShelfBand from '../components/common/ShelfBand.vue'
import StatSlip, { type SlipItem } from '../components/common/StatSlip.vue'
import EmptyState from '../components/common/EmptyState.vue'
import { usePlanogramStore } from '../stores/planogramStore'
import { useAuthStore } from '../stores/authStore'
import { MESSAGES } from '../constants/messages'
import { SCHEDULE_PHASE_LABELS, SLOT_STATUS_LABELS } from '../constants/enums'
import type { SchedulePhase } from '../types/domain'

const store = usePlanogramStore()
const auth = useAuthStore()
const working = ref(false)

const STATE_LABELS: Record<string, string> = { available: '可用位', occupied: '占用中', conflict: '冲突' }

function isoDay(offset: number): string {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const winFrom = ref(isoDay(0))
const winTo = ref(isoDay(29))
const checkPlan = ref('')
const effDate = ref(isoDay(1))
const endDate = ref(isoDay(14))

const overview = computed(() => store.overview)
const preview = computed(() => store.preview)
const plans = computed(() => store.plans)
const canWrite = computed(() => auth.can('planogram:write'))
const canPublish = computed(() => auth.can('planogram:publish'))

const stats = computed<SlipItem[]>(() => {
  const row = overview.value
  if (!row) return []
  return [
    { label: '可用位', value: row.summary.available, unit: '个', tone: 'mint' },
    { label: '占用中', value: row.summary.occupied, unit: '个' },
    {
      label: '冲突组合',
      value: row.summary.conflicts,
      unit: '组',
      tone: row.summary.conflicts ? undefined : 'mint',
      hint: row.summary.conflicts ? '发布前必须消解' : '无冲突',
    },
    { label: '窗口', value: `${row.window.from} → ${row.window.to}`, hint: `今天 ${row.today}` },
  ]
})

function phaseChip(phase: SchedulePhase): string {
  if (phase === 'active') return 'chip--mint'
  if (phase === 'pending') return 'chip--tag'
  if (phase === 'replaced') return 'chip--alert'
  return ''
}

function stateChip(state: string): string {
  if (state === 'available') return 'chip--mint'
  if (state === 'conflict') return 'chip--alert'
  return 'chip--tag'
}

async function reload() {
  working.value = true
  try {
    await store.loadOverview(winFrom.value || undefined, winTo.value || undefined)
  } catch {
    /* store 已记录 */
  } finally {
    working.value = false
  }
}

async function runPreview() {
  if (!checkPlan.value) return
  working.value = true
  try {
    await store.runPreview(checkPlan.value, { effective_date: effDate.value, end_date: endDate.value })
  } catch {
    /* store 已记录 */
  } finally {
    working.value = false
  }
}

async function apply() {
  if (!preview.value?.ok) return
  working.value = true
  try {
    await store.publish(preview.value.plan_code, { effective_date: effDate.value, end_date: endDate.value })
    store.preview = null
    await reload()
  } catch {
    /* store 已记录 */
  } finally {
    working.value = false
  }
}

onMounted(async () => {
  await Promise.all([store.load(), reload()])
  if (!checkPlan.value && plans.value.length) checkPlan.value = plans.value[0].plan_code
})
</script>

<style scoped>
.schedule {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.notice {
  margin-top: 12px;
  padding: 8px 12px;
  font-size: 12px;
  border: 1px dashed var(--shelf);
  background: var(--paper-2);
}
.notice--tag {
  border-color: var(--tag-deep);
  background: var(--tag-pale);
}
.notice--mint {
  border-color: var(--mint-deep);
  background: var(--mint-pale);
  color: var(--mint-ink);
}
.notice--alert {
  border-color: var(--alert);
  background: var(--alert-pale);
  color: var(--alert);
}
.win {
  display: flex;
  align-items: center;
  gap: 6px;
}
.win__label {
  font-size: 10.5px;
  color: var(--ink-faint);
  white-space: nowrap;
}
.win .field {
  width: 138px;
  font-size: 12px;
  padding: 6px 8px;
}
.strip {
  margin-top: 14px;
}
.muted {
  font-size: 12px;
  color: var(--ink-faint);
  padding: 6px 0;
}

/* 货架位占用总览 */
.positions {
  display: flex;
  flex-direction: column;
}
.position {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  gap: 14px;
  padding: 10px 2px 12px;
  border-bottom: var(--shelf-line);
}
.position:last-child {
  border-bottom: 0;
}
.position__marker {
  display: flex;
  flex-direction: column;
  gap: 3px;
  align-items: flex-start;
}
.position__group {
  font-family: var(--tag-font);
  font-size: 14px;
  font-weight: 700;
}
.position__zone {
  font-size: 11px;
  color: var(--ink-soft);
}
.position--available .position__group {
  color: var(--mint-ink);
}
.position--conflict .position__group {
  color: var(--alert);
}
.position__body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.position__free {
  font-size: 11.5px;
  color: var(--mint-ink);
  border: 1px dashed var(--mint-deep);
  background: var(--mint-pale);
  padding: 5px 9px;
  align-self: flex-start;
}
.occ {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 11.5px;
  border-left: 2px solid var(--shelf);
  padding-left: 9px;
}
.occ__plan {
  font-weight: 600;
}
.occ__rule {
  color: var(--ink-soft);
  min-width: 0;
}
.occ__dates {
  color: var(--ink);
}

/* 冲突组合 */
.conflicts {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.conflict {
  border: 1px solid var(--alert);
  background: var(--alert-pale);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.conflict__head {
  display: flex;
  align-items: baseline;
  gap: 9px;
  flex-wrap: wrap;
}
.conflict__group {
  font-size: 12.5px;
  font-weight: 600;
}
.conflict__window {
  font-size: 11px;
  color: var(--alert);
}
.conflict__plans {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.conflict__rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.conflict__rows li {
  display: flex;
  align-items: baseline;
  gap: 9px;
  flex-wrap: wrap;
  font-size: 11.5px;
  border-left: 2px solid var(--alert);
  padding-left: 8px;
}

/* 发布核对 */
.checker {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.checker__form {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}
.checker__field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.checker__field .field {
  width: 220px;
  font-size: 12px;
}
.checker__field input.field {
  width: 150px;
}
.checker__actions {
  display: flex;
  gap: 8px;
}
.counts {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
}
.replace-list {
  border: 1px dashed var(--tag-deep);
  background: var(--tag-pale);
  padding: 9px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.replace-list__title {
  font-size: 11px;
  color: oklch(42% 0.08 80);
}
.replace {
  display: flex;
  align-items: baseline;
  gap: 9px;
  flex-wrap: wrap;
  font-size: 11.5px;
}
.replace__rule {
  font-weight: 500;
}
.replace__old {
  text-decoration: line-through;
  color: var(--ink-faint);
}
.replace__arrow {
  color: var(--tag-deep);
}
.replace__new {
  color: var(--mint-ink);
  font-weight: 600;
}
.final th,
.final td {
  text-align: left;
  padding: 7px 10px 7px 0;
  border-bottom: var(--shelf-line);
  font-size: 12px;
}
.final th {
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-faint);
  font-weight: 500;
}
.final__group {
  font-family: var(--tag-font);
  font-weight: 700;
  margin-right: 8px;
}
.final__shelf {
  font-size: 10.5px;
  color: var(--ink-faint);
}
@media (max-width: 767px) {
  .position {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .checker__field .field,
  .checker__field input.field {
    width: 100%;
  }
  .checker__field {
    width: 100%;
  }
}
</style>
