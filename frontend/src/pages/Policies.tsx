import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { PolicyRule, RuleType } from '@/lib/api'
import { PolicyRuleForm } from '@/components/PolicyRuleForm'
import { RULE_TYPE_LABELS, summarizeRule } from '@/lib/policyRules'

const SEVERITY_STYLES: Record<PolicyRule['severity'], string> = {
  low: 'border-[#262626] text-[#a3a3a3]',
  medium: 'border-yellow-900 text-yellow-400',
  high: 'border-red-900 text-red-400',
}

export function Policies() {
  const [rules, setRules] = useState<PolicyRule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [form, setForm] = useState<{ mode: 'create' } | { mode: 'edit'; rule: PolicyRule } | null>(null)

  useEffect(() => {
    api
      .listPolicyRules()
      .then((data) => setRules(data))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  const toggle = async (rule: PolicyRule) => {
    setError(null)
    setBusyId(rule.id)
    try {
      const updated = await api.updatePolicyRule(rule.id, { is_active: !rule.is_active })
      setRules((rs) => rs.map((r) => (r.id === updated.id ? updated : r)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update rule')
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (rule: PolicyRule) => {
    setError(null)
    setBusyId(rule.id)
    try {
      await api.deletePolicyRule(rule.id)
      setRules((rs) => rs.filter((r) => r.id !== rule.id))
      setConfirmId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete rule')
    } finally {
      setBusyId(null)
    }
  }

  const btn =
    'text-xs px-3 py-1 rounded border border-[#262626] text-[#e5e5e5] hover:bg-[#1a1a1a] transition-colors disabled:opacity-30 disabled:hover:bg-transparent'

  return (
    <div className="flex flex-col h-full">
      <div className="px-8 py-5 border-b border-[#262626] flex items-center justify-between">
        <div>
          <h1 className="text-base font-semibold text-white">Policies</h1>
          <p className="text-xs text-[#737373] mt-0.5">
            Changes apply to new receipts. Re-analyze older receipts to apply them.
          </p>
        </div>
        <button
          className={btn}
          onClick={() => {
            setError(null)
            setForm({ mode: 'create' })
          }}
        >
          Add rule
        </button>
      </div>

      {error && <div className="px-8 py-3 text-sm text-red-400">{error}</div>}

      <div className="flex-1 overflow-auto">
        {form && (
          <PolicyRuleForm
            key={form.mode === 'edit' ? form.rule.id : 'new'}
            mode={form.mode}
            rule={form.mode === 'edit' ? form.rule : undefined}
            onCancel={() => setForm(null)}
            onSaved={(saved) => {
              setRules((rs) =>
                rs.some((r) => r.id === saved.id)
                  ? rs.map((r) => (r.id === saved.id ? saved : r))
                  : [...rs, saved],
              )
              setForm(null)
            }}
          />
        )}
        {loading ? (
          <div className="p-8 text-sm text-[#737373]">Loading…</div>
        ) : rules.length === 0 ? (
          <div className="p-8 text-sm text-[#737373]">No policy rules yet.</div>
        ) : (
          <ul>
            {rules.map((rule) => {
              const label = RULE_TYPE_LABELS[rule.rule_type as RuleType] ?? rule.rule_type
              const summary = summarizeRule(rule)
              const busy = busyId === rule.id
              return (
                <li
                  key={rule.id}
                  className="px-8 py-3 border-b border-[#1a1a1a] flex items-center gap-4"
                >
                  <button
                    role="switch"
                    aria-checked={rule.is_active}
                    aria-label={`${rule.is_active ? 'Disable' : 'Enable'} ${rule.rule_name}`}
                    disabled={busy}
                    onClick={() => toggle(rule)}
                    className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
                      rule.is_active ? 'bg-green-600' : 'bg-[#262626]'
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                        rule.is_active ? 'left-[18px]' : 'left-0.5'
                      }`}
                    />
                  </button>

                  <div className={`flex-1 min-w-0 ${rule.is_active ? '' : 'opacity-50'}`}>
                    <p className="text-sm text-[#e5e5e5] truncate">{rule.rule_name}</p>
                    <p className="text-xs text-[#737373] mt-0.5">
                      {label}
                      {summary && <span className="mono"> · {summary}</span>}
                    </p>
                  </div>

                  <span
                    className={`text-xs px-2 py-0.5 rounded border ${SEVERITY_STYLES[rule.severity]}`}
                  >
                    {rule.severity}
                  </span>

                  {confirmId === rule.id ? (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[#e5e5e5]">
                        Delete '{rule.rule_name}'? This can't be undone.
                      </span>
                      <button className={btn} disabled={busy} onClick={() => setConfirmId(null)}>
                        Cancel
                      </button>
                      <button
                        className="text-xs px-3 py-1 rounded border border-red-900 text-red-400 hover:bg-red-950 transition-colors disabled:opacity-30"
                        disabled={busy}
                        onClick={() => remove(rule)}
                      >
                        Delete
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      {rule.rule_type in RULE_TYPE_LABELS && (
                        <button
                          className={btn}
                          disabled={busy}
                          onClick={() => {
                            setError(null)
                            setForm({ mode: 'edit', rule })
                          }}
                        >
                          Edit
                        </button>
                      )}
                      <button
                        className={btn}
                        disabled={busy}
                        onClick={() => {
                          setError(null)
                          setConfirmId(rule.id)
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
