const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

import { supabase } from '@/lib/supabase'
import { getActiveTenantId } from '@/lib/activeTenant'

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) return {}
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  const tenantId = getActiveTenantId()
  if (tenantId != null) headers['X-Tenant-ID'] = String(tenantId)
  return headers
}

async function handleUnauthorized(res: Response): Promise<void> {
  if (res.status === 401) {
    await supabase.auth.signOut()
    window.location.assign('/login')
  }
}

export interface FraudFlag {
  id: number
  flag_type: string
  severity: 'low' | 'medium' | 'high'
  description: string
  confidence_score: number
  details: Record<string, unknown> | null
  created_at: string
}

export interface Receipt {
  id: number
  employee_id: number | null
  merchant: string | null
  amount: number | null
  transaction_date: string | null
  category: string | null
  items: { line_items?: { description: string; amount: number }[]; transaction_time?: string; raw_text?: string } | null
  ocr_confidence: number | null
  fraud_score: number | null
  risk_level: 'low' | 'medium' | 'high' | null
  status: 'pending' | 'flagged' | 'approved' | 'rejected' | 'analyzed'
  explanation: string | null
  image_path: string
  image_hash: string | null
  created_at: string
  analyzed_at: string | null
  reviewed_at: string | null
  fraud_flags: FraudFlag[]
}

export interface ReceiptListResponse {
  receipts: Receipt[]
  total: number
  page: number
  per_page: number
}

export interface ReviewResponse extends Receipt {
  original_receipt_id: number | null
}

export interface Membership {
  tenant_id: number
  name: string
  role: string
}

export interface MeResponse {
  user_id: string
  email: string | null
  memberships: Membership[]
}

export interface TenantResponse {
  id: number
  name: string
  created_at: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(await authHeaders()),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    await handleUnauthorized(res)
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

export const api = {
  getMe: (): Promise<MeResponse> => request('/api/v1/auth/me'),

  createTenant: (name: string): Promise<TenantResponse> =>
    request('/api/v1/tenants', { method: 'POST', body: JSON.stringify({ name }) }),

  uploadReceipt: async (file: File): Promise<Receipt> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE_URL}/api/v1/receipts/upload`, {
      method: 'POST',
      body: form,
      headers: await authHeaders(),
    })
    if (!res.ok) {
      await handleUnauthorized(res)
      const text = await res.text()
      throw new Error(`${res.status}: ${text}`)
    }
    return res.json()
  },

  getReceipt: (id: number): Promise<Receipt> =>
    request(`/api/v1/receipts/${id}`),

  listReceipts: (page = 1, pageSize = 20, status?: string): Promise<ReceiptListResponse> => {
    const params = new URLSearchParams({ page: String(page), per_page: String(pageSize) })
    if (status) params.set('status', status)
    return request(`/api/v1/receipts?${params}`)
  },

  analyzeReceipt: (id: number): Promise<Receipt> =>
    request(`/api/v1/receipts/${id}/analyze`, { method: 'POST', body: '{}' }),

  reviewReceipt: (
    id: number,
    decision: 'approved' | 'rejected',
    note?: string,
  ): Promise<ReviewResponse> =>
    request(`/api/v1/receipts/${id}/review`, {
      method: 'PATCH',
      body: JSON.stringify({ decision, note }),
    }),

  explainReceipt: (id: number): Promise<{ explanation: string }> =>
    request(`/api/v1/receipts/${id}/explain`, { method: 'POST' }),
}
