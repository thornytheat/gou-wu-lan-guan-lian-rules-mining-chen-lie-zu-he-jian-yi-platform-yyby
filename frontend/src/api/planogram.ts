import http from './http'
import type { Plan, PlanSummary, ScheduleOverview, SchedulePreview } from '../types/domain'

export interface ScheduleWindow {
  effective_date?: string | null
  end_date?: string | null
}

export async function fetchPlans(): Promise<{ plans: PlanSummary[]; count: number }> {
  const { data } = await http.get('/planogram')
  return data
}

export async function fetchPlan(planCode: string): Promise<Plan> {
  const { data } = await http.get<Plan>(`/planogram/${planCode}`)
  return data
}

export async function generatePlan(runId?: string): Promise<Plan> {
  const { data } = await http.post<Plan>('/planogram/generate', { run_id: runId ?? null })
  return data
}

export async function publishPlan(planCode: string, window: ScheduleWindow = {}): Promise<Plan> {
  const { data } = await http.post<Plan>(`/planogram/${planCode}/publish`, {
    effective_date: window.effective_date ?? null,
    end_date: window.end_date ?? null,
  })
  return data
}

export async function previewSchedule(planCode: string, window: ScheduleWindow = {}): Promise<SchedulePreview> {
  const { data } = await http.post<SchedulePreview>(`/planogram/${planCode}/schedule/preview`, {
    effective_date: window.effective_date ?? null,
    end_date: window.end_date ?? null,
  })
  return data
}

export async function fetchScheduleOverview(dateFrom?: string, dateTo?: string): Promise<ScheduleOverview> {
  const params: Record<string, string> = {}
  if (dateFrom) params.date_from = dateFrom
  if (dateTo) params.date_to = dateTo
  const { data } = await http.get<ScheduleOverview>('/planogram/schedule/overview', { params })
  return data
}

export async function updateSlot(
  slotId: string,
  status: 'draft' | 'published' | 'archived',
  note = '',
): Promise<Plan> {
  const { data } = await http.patch<Plan>(`/planogram/slots/${slotId}`, { status, note })
  return data
}

export function planogramExportUrl(planCode: string): string {
  return `/api/planogram/${planCode}/export`
}
