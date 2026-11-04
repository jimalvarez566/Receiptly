import { useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '@/lib/api'
import type { FraudFlag, Receipt } from '@/lib/api'
import { RiskBadge } from '@/components/RiskBadge'
import { StatusBadge } from '@/components/StatusBadge'

// Shadow-mode flags come from rules still being evaluated: shown, but not scored
function isShadow(flag: FraudFlag) {
  return flag.details?.shadow === true
}

function fmt(amount: number | string | null) {
  if (amount == null) return '—'
  return '$' + Number(amount).toFixed(2)
}

function fmtDatetime(d: string | null) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

type ActionState = 'idle' | 'loading' | 'error'

export function ReceiptDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [actionState, setActionState] = useState<ActionState>('idle')
  const [actionError, setActionError] = useState<string | null>(null)

  const [explanation, setExplanation] = useState<string | null>(null)
  const [explainVisible, setExplainVisible] = useState(false)
  const [explainLoading, setExplainLoading] = useState(false)
  const [explainError, setExplainError] = useState<string | null>(null)
  const [explanationInitialized, setExplanationInitialized] = useState(false)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    api
      .getReceipt(Number(id))
      .then(setReceipt)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [id])

  // Pre-populate explanation from initial GET response (once per receipt load)
  useEffect(() => {
    if (receipt && !explanationInitialized) {
      setExplanationInitialized(true)
      if (receipt.explanation) {
        setExplanation(receipt.explanation)
        setExplainVisible(true)
      }
    }
  }, [receipt, explanationInitialized])

  const runAction = async (fn: () => Promise<Receipt>) => {
    setActionState('loading')
    setActionError(null)
    try {
      const updated = await fn()
      setReceipt(updated)
      setActionState('idle')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Action failed')
      setActionState('error')
    }
  }

  const approve = () => runAction(() => api.reviewReceipt(Number(id), 'approved'))
  const reject = () => runAction(() => api.reviewReceipt(Number(id), 'rejected'))
  const reanalyze = () => {
    setExplanation(null)
    setExplainVisible(false)
    setExplainError(null)
    setExplanationInitialized(false)
    runAction(() => api.analyzeReceipt(Number(id)))
  }

  const handleExplain = async () => {
    if (explanation !== null) {
      setExplainVisible((v) => !v)
      return
    }
    setExplainLoading(true)
    setExplainError(null)
    try {
      const result = await api.explainReceipt(Number(id))
      setExplanation(result.explanation)
      setExplainVisible(true)
    } catch (err) {
      setExplainError(err instanceof Error ? err.message : 'Failed to get explanation')
    } finally {
      setExplainLoading(false)
    }
  }

  if (loading) {
    return <div className="p-8 text-sm text-[#737373]">Loading…</div>
  }

  if (error) {
    return (
      <div className="p-8">
        <p className="text-sm text-red-400 mb-3">{error}</p>
        <button
          onClick={() => navigate('/receipts')}
          className="text-xs text-[#737373] underline underline-offset-2 hover:text-white"
        >
          ← Back to receipts
        </button>
      </div>
    )
  }

  if (!receipt) return null

  const lineItems: { description: string; amount: number }[] =
    receipt.items?.line_items ?? []

  const scoredFlags = receipt.fraud_flags.filter((f) => !isShadow(f))
  const shadowCount = receipt.fraud_flags.length - scoredFlags.length

  const duplicateFlag = receipt.fraud_flags.find((f) => f.flag_type === 'duplicate')
  const originalReceiptId =
    duplicateFlag?.details && typeof duplicateFlag.details === 'object'
      ? (duplicateFlag.details as Record<string, unknown>).existing_receipt_id
      : null

  const isBusy = actionState === 'loading'
  const canReview = receipt.status !== 'approved' && receipt.status !== 'rejected'

  return (
    <div className="max-w-2xl p-8">
      {/* Back */}
      <button
        onClick={() => navigate('/receipts')}
        className="text-xs text-[#737373] hover:text-white mb-6 block"
      >
        ← Receipts
      </button>

      {/* Header — fraud score prominent */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <span className="mono text-4xl font-bold text-white">
              {receipt.fraud_score ?? '—'}
            </span>
            <RiskBadge level={receipt.risk_level} />
          </div>
          <p className="text-xs text-[#737373]">fraud score</p>
        </div>
        <div className="text-right">
          <div className="mb-1"><StatusBadge status={receipt.status} /></div>
          <p className="mono text-xs text-[#404040]">#{receipt.id}</p>
        </div>
      </div>

      {/* OCR fields */}
      <section className="border border-[#262626] rounded mb-4">
        <div className="px-4 py-2.5 border-b border-[#262626]">
          <h2 className="text-xs font-medium text-[#737373] uppercase tracking-wider">Receipt Data</h2>
        </div>
        <div className="divide-y divide-[#1a1a1a]">
          <Row label="Merchant" value={receipt.merchant} />
          <Row label="Amount" value={fmt(receipt.amount)} mono />
          <Row label="Date" value={receipt.transaction_date ?? '—'} mono />
          <Row label="Category" value={receipt.category} />
          <Row label="OCR Confidence" value={receipt.ocr_confidence != null ? `${Number(receipt.ocr_confidence).toFixed(0)}%` : '—'} mono />
          <Row label="Uploaded" value={fmtDatetime(receipt.created_at)} mono />
          <Row label="Analyzed" value={fmtDatetime(receipt.analyzed_at)} mono />
        </div>
      </section>

      {/* Line items */}
      {lineItems.length > 0 && (
        <section className="border border-[#262626] rounded mb-4">
          <div className="px-4 py-2.5 border-b border-[#262626]">
            <h2 className="text-xs font-medium text-[#737373] uppercase tracking-wider">Line Items</h2>
          </div>
          <div className="divide-y divide-[#1a1a1a]">
            {lineItems.map((item, i) => (
              <div key={i} className="px-4 py-2.5 flex justify-between text-sm">
                <span className="text-[#e5e5e5]">{item.description}</span>
                <span className="mono text-[#737373]">{fmt(item.amount)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Fraud flags */}
      <section className="border border-[#262626] rounded mb-4">
        <div className="px-4 py-2.5 border-b border-[#262626]">
          <h2 className="text-xs font-medium text-[#737373] uppercase tracking-wider">
            Fraud Flags
            {scoredFlags.length > 0 && (
              <span className="ml-2 text-red-400">{scoredFlags.length}</span>
            )}
            {shadowCount > 0 && (
              <span className="ml-2 text-[#525252]">+{shadowCount} shadow</span>
            )}
          </h2>
        </div>
        {receipt.fraud_flags.length === 0 ? (
          <p className="px-4 py-3 text-sm text-[#404040]">No flags.</p>
        ) : (
          <div className="divide-y divide-[#1a1a1a]">
            {receipt.fraud_flags.map((flag) => (
              <div key={flag.id} className={`px-4 py-3 ${isShadow(flag) ? 'opacity-60' : ''}`}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="mono text-xs text-[#e5e5e5]">
                    {flag.flag_type}
                    {typeof flag.details?.signal === 'string' &&
                      ` · ${flag.details.period === 'week' ? 'weekly ' : flag.details.period === 'month' ? 'monthly ' : ''}${flag.details.signal.replace('_', ' ')}`}
                  </span>
                  <SeverityDot severity={flag.severity} />
                  <span className={`text-xs ${severityColor(flag.severity)}`}>{flag.severity}</span>
                  {isShadow(flag) && (
                    <span
                      title="Rule in shadow mode: recorded for evaluation, not counted in the score"
                      className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-[#262626] text-[#737373]"
                    >
                      shadow
                    </span>
                  )}
                  {flag.confidence_score != null && (
                    <span className="mono text-xs text-[#404040] ml-auto">
                      {Number(flag.confidence_score).toFixed(0)}% confidence
                    </span>
                  )}
                </div>
                {flag.description && (
                  <p className="text-xs text-[#737373]">{flag.description}</p>
                )}
                {flag.flag_type === 'duplicate' && originalReceiptId != null && (
                  <Link
                    to={`/receipts/${originalReceiptId}`}
                    className="text-xs text-blue-400 underline underline-offset-2 mt-1 block hover:text-blue-300"
                  >
                    View original → #{String(originalReceiptId)}
                  </Link>
                )}
                {typeof flag.details?.matching_receipt_id === 'number' && (
                  <Link
                    to={`/receipts/${flag.details.matching_receipt_id}`}
                    className="text-xs text-blue-400 underline underline-offset-2 mt-1 block hover:text-blue-300"
                  >
                    View matching receipt → #{flag.details.matching_receipt_id}
                  </Link>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Explain feature — explains the score, so only scored flags count */}
      {scoredFlags.length > 0 && (
        <div className="mb-4">
          <button
            onClick={handleExplain}
            disabled={explainLoading}
            className="text-xs px-3 py-1.5 rounded border border-[#262626] text-[#737373] hover:text-white hover:border-[#404040] transition-colors disabled:opacity-40 flex items-center gap-2"
          >
            {explainLoading && (
              <span className="inline-block w-3 h-3 border border-[#737373] border-t-white rounded-full animate-spin" />
            )}
            {explainLoading
              ? 'Fetching explanation…'
              : explanation !== null && explainVisible
              ? 'Hide explanation'
              : 'Why this score?'}
          </button>

          {explainError && (
            <p className="mt-2 text-xs text-red-400">{explainError}</p>
          )}

          {explanation !== null && explainVisible && (
            <div className="mt-3 border border-[#262626] rounded px-4 py-3">
              <p className="text-xs font-medium text-[#737373] uppercase tracking-wider mb-2">AI Explanation</p>
              <p className="text-sm text-[#e5e5e5] leading-relaxed">{explanation}</p>
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2">
        {canReview && (
          <>
            <ActionButton onClick={approve} disabled={isBusy} variant="approve">
              Approve
            </ActionButton>
            <ActionButton onClick={reject} disabled={isBusy} variant="reject">
              Reject
            </ActionButton>
            <span className="text-[#262626]">|</span>
          </>
        )}
        <ActionButton onClick={reanalyze} disabled={isBusy} variant="neutral">
          {actionState === 'loading' ? 'Analyzing…' : 'Re-analyze'}
        </ActionButton>
      </div>

      {actionError && (
        <p className="mt-3 text-xs text-red-400">{actionError}</p>
      )}
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div className="px-4 py-2.5 flex justify-between gap-4 text-sm">
      <span className="text-[#737373] shrink-0">{label}</span>
      <span className={`text-right text-[#e5e5e5] ${mono ? 'mono' : ''}`}>
        {value ?? <span className="text-[#404040]">—</span>}
      </span>
    </div>
  )
}

function SeverityDot({ severity }: { severity: string }) {
  const color =
    severity === 'high' ? 'bg-red-400' :
    severity === 'medium' ? 'bg-yellow-400' :
    'bg-green-400'
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${color}`} />
}

function severityColor(severity: string) {
  return severity === 'high' ? 'text-red-400' :
    severity === 'medium' ? 'text-yellow-400' :
    'text-green-400'
}

function ActionButton({
  onClick,
  disabled,
  variant,
  children,
}: {
  onClick: () => void
  disabled: boolean
  variant: 'approve' | 'reject' | 'neutral'
  children: React.ReactNode
}) {
  const base = 'text-xs px-3 py-1.5 rounded border transition-colors disabled:opacity-40'
  const styles = {
    approve: 'border-green-400/40 text-green-400 hover:bg-green-400/10',
    reject: 'border-red-400/40 text-red-400 hover:bg-red-400/10',
    neutral: 'border-[#262626] text-[#737373] hover:text-white hover:border-[#404040]',
  }
  return (
    <button onClick={onClick} disabled={disabled} className={`${base} ${styles[variant]}`}>
      {children}
    </button>
  )
}
