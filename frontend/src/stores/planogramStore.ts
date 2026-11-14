import { defineStore } from 'pinia'
import {
  fetchPlan,
  fetchPlans,
  fetchScheduleOverview,
  generatePlan,
  previewSchedule,
  publishPlan,
  type ScheduleWindow,
} from '../api/planogram'
import type { Plan, PlanSummary, ScheduleOverview, SchedulePreview } from '../types/domain'

interface PlanState {
  plans: PlanSummary[]
  current: Plan | null
  overview: ScheduleOverview | null
  preview: SchedulePreview | null
  loading: boolean
  working: boolean
  error: string
  notice: string
}

export const usePlanogramStore = defineStore('planogram', {
  state: (): PlanState => ({
    plans: [],
    current: null,
    overview: null,
    preview: null,
    loading: false,
    working: false,
    error: '',
    notice: '',
  }),
  actions: {
    async load() {
      this.loading = true
      this.error = ''
      try {
        const result = await fetchPlans()
        this.plans = result.plans
        if (this.plans.length) {
          const target =
            this.current?.plan_code && this.plans.some((item) => item.plan_code === this.current?.plan_code)
              ? this.current.plan_code
              : this.plans[0].plan_code
          this.current = await fetchPlan(target)
        } else {
          this.current = null
        }
      } catch (error) {
        this.error = (error as Error).message
      } finally {
        this.loading = false
      }
    },
    async open(planCode: string) {
      this.loading = true
      this.error = ''
      try {
        this.current = await fetchPlan(planCode)
      } catch (error) {
        this.error = (error as Error).message
      } finally {
        this.loading = false
      }
    },
    async generate(runId?: string) {
      this.working = true
      this.error = ''
      this.notice = ''
      try {
        this.current = await generatePlan(runId)
        this.notice = `已生成 ${this.current.slot_count} 个陈列建议位`
        await this.load()
        return this.current
      } catch (error) {
        this.error = (error as Error).message
        throw error
      } finally {
        this.working = false
      }
    },
    async publish(planCode: string, window: ScheduleWindow = {}) {
      this.working = true
      this.error = ''
      this.notice = ''
      try {
        this.current = await publishPlan(planCode, window)
        const counts = this.current.schedule?.counts
        this.notice = counts
          ? `建议单已发布：新增占用 ${counts.created} 条、替换 ${counts.replaced} 条、沿用 ${counts.keep + counts.unchanged} 条`
          : '建议单已发布，店长端可见'
        await this.load()
        return this.current
      } catch (error) {
        this.error = (error as Error).message
        throw error
      } finally {
        this.working = false
      }
    },
    async loadOverview(dateFrom?: string, dateTo?: string) {
      this.error = ''
      try {
        this.overview = await fetchScheduleOverview(dateFrom, dateTo)
        return this.overview
      } catch (error) {
        this.error = (error as Error).message
        throw error
      }
    },
    async runPreview(planCode: string, window: ScheduleWindow = {}) {
      this.working = true
      this.error = ''
      this.notice = ''
      try {
        this.preview = await previewSchedule(planCode, window)
        return this.preview
      } catch (error) {
        this.error = (error as Error).message
        throw error
      } finally {
        this.working = false
      }
    },
  },
})
