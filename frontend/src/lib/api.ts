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

export interface ReceiptImageUrl {
  url: string
  expires_in: number
  kind: 'image' | 'pdf'
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
  role: 'admin' | 'member'
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

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`
  try {
    const body = await res.json()
    const detail = body?.detail
    if (typeof detail === 'string' && detail) {
      message = detail
    } else if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (typeof d?.msg === 'string' ? d.msg : null))
        .filter((m): m is string => m !== null)
      if (msgs.length > 0) message = msgs.join('; ')
    }
  } catch {
    /* non-JSON body: keep fallback */
  }
  return new ApiError(res.status, message)
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
    throw await toApiError(res)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export type RuleType =
  | 'amount_limit'
  | 'future_date'
  | 'vendor_category'
  | 'round_number'
  | 'short_window_duplicate'

export interface PolicyRule {
  id: number
  rule_name: string
  rule_type: string
  severity: 'low' | 'medium' | 'high'
  parameters: Record<string, unknown>
  is_active: boolean
  created_at: string
}

export const api = {
  listPolicyRules: (): Promise<PolicyRule[]> => request('/api/v1/policy-rules'),

  createPolicyRule: (body: {
    rule_name: string
    rule_type: RuleType
    severity: PolicyRule['severity']
    parameters: Record<string, unknown>
    is_active?: boolean
  }): Promise<PolicyRule> =>
    request('/api/v1/policy-rules', { method: 'POST', body: JSON.stringify(body) }),

  updatePolicyRule: (
    id: number,
    body: Partial<Pick<PolicyRule, 'rule_name' | 'severity' | 'parameters' | 'is_active'>>,
  ): Promise<PolicyRule> =>
    request(`/api/v1/policy-rules/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  deletePolicyRule: (id: number): Promise<void> =>
    request(`/api/v1/policy-rules/${id}`, { method: 'DELETE' }),

  updateMemberRole: (
    tenantId: number,
    userId: string,
    role: 'admin' | 'member',
  ): Promise<{ user_id: string; role: 'admin' | 'member' }> =>
    request(`/api/v1/tenants/${tenantId}/members/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }),

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
      throw await toApiError(res)
    }
    return res.json()
  },

  getReceipt: (id: number): Promise<Receipt> =>
    request(`/api/v1/receipts/${id}`),
  getReceiptImageUrl: (id: number): Promise<ReceiptImageUrl> =>
    request(`/api/v1/receipts/${id}/image-url`),

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
