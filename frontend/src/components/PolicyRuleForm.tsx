import { useState } from 'react'
import { api } from '@/lib/api'
import type { PolicyRule, RuleType } from '@/lib/api'
import {
  RULE_TYPE_LABELS,
  categoriesToText,
  defaultParameters,
  textToCategories,
} from '@/lib/policyRules'

interface Props {
  mode: 'create' | 'edit'
  rule?: PolicyRule
  onSaved: (r: PolicyRule) => void
  onCancel: () => void
}

const TYPES = Object.keys(RULE_TYPE_LABELS) as RuleType[]

const input =
  'w-full bg-[#0a0a0a] border border-[#262626] rounded px-3 py-1.5 text-sm text-[#e5e5e5] focus:outline-none focus:border-[#525252]'
const label = 'block text-xs text-[#737373] mb-1'

function str(v: unknown): string {
  return typeof v === 'number' || typeof v === 'string' ? String(v) : ''
}

function toFields(type: RuleType, p: Record<string, unknown>) {
  const cats = p.allowed_categories
  return {
    limit: str(p.limit),
    category: str(p.category),
    minAmount: str(p.min_amount),
    windowHours: str(p.window_hours),
    tolerance: str(p.amount_tolerance_pct),
    catText:
      type === 'vendor_category' && cats && typeof cats === 'object'
        ? categoriesToText(cats as Record<string, string[]>)
        : '',
  }
}

export function PolicyRuleForm({ mode, rule, onSaved, onCancel }: Props) {
  const initialType = (rule?.rule_type as RuleType | undefined) ?? 'amount_limit'
  const [name, setName] = useState(rule?.rule_name ?? '')
  const [severity, setSeverity] = useState<PolicyRule['severity']>(rule?.severity ?? 'medium')
  const [type, setType] = useState<RuleType>(initialType)
  const [f, setF] = useState(() =>
    toFields(initialType, rule ? rule.parameters : defaultParameters(initialType)),
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }))

  const changeType = (t: RuleType) => {
    setType(t)
    setF(toFields(t, defaultParameters(t)))
  }

  const buildParameters = (): Record<string, unknown> => {
    switch (type) {
      case 'amount_limit': {
        const p: Record<string, unknown> = { limit: Number(f.limit) }
        if (f.category.trim()) p.category = f.category.trim()
        return p
      }
      case 'future_date':
        return {}
      case 'vendor_category':
        return { allowed_categories: textToCategories(f.catText) }
      case 'round_number':
        return { min_amount: Number(f.minAmount) }
      case 'short_window_duplicate':
        return {
          window_hours: Number(f.windowHours),
          amount_tolerance_pct: Number(f.tolerance),
        }
    }
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const parameters = buildParameters()
      const saved =
        mode === 'edit' && rule
          ? await api.updatePolicyRule(rule.id, {
              rule_name: name.trim(),
              severity,
              parameters,
            })
          : await api.createPolicyRule({
              rule_name: name.trim(),
              rule_type: type,
              severity,
              parameters,
            })
      onSaved(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save rule')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mx-8 my-4 p-4 rounded border border-[#262626] bg-[#0f0f0f] space-y-3 max-w-xl"
    >
      <h2 className="text-sm font-semibold text-white">
        {mode === 'create' ? 'Add rule' : 'Edit rule'}
      </h2>

      <div>
        <label className={label} htmlFor="rule-name">
          Name
        </label>
        <input
          id="rule-name"
          className={input}
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={label} htmlFor="rule-type">
            Type
          </label>
          <select
            id="rule-type"
            className={input}
            value={type}
            disabled={mode === 'edit'}
            onChange={(e) => changeType(e.target.value as RuleType)}
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {RULE_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={label} htmlFor="rule-severity">
            Severity
          </label>
          <select
            id="rule-severity"
            className={input}
            value={severity}
            onChange={(e) => setSeverity(e.target.value as PolicyRule['severity'])}
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </div>
      </div>

      {type === 'amount_limit' && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label} htmlFor="f-limit">
              Limit ($)
            </label>
            <input
              id="f-limit"
              type="number"
              step="any"
              min="0"
              className={input}
              value={f.limit}
              onChange={(e) => set('limit', e.target.value)}
              required
            />
          </div>
          <div>
            <label className={label} htmlFor="f-category">
              Category (optional)
            </label>
            <input
              id="f-category"
              className={input}
              maxLength={50}
              value={f.category}
              onChange={(e) => set('category', e.target.value)}
            />
          </div>
        </div>
      )}

      {type === 'future_date' && (
        <p className="text-xs text-[#737373]">This rule has no settings.</p>
      )}

      {type === 'vendor_category' && (
        <div>
          <label className={label} htmlFor="f-cats">
            Allowed categories (one per line: name: keyword1, keyword2)
          </label>
          <textarea
            id="f-cats"
            rows={5}
            className={`${input} mono`}
            value={f.catText}
            onChange={(e) => set('catText', e.target.value)}
          />
        </div>
      )}

      {type === 'round_number' && (
        <div>
          <label className={label} htmlFor="f-min">
            Minimum amount ($)
          </label>
          <input
            id="f-min"
            type="number"
            step="any"
            min="0"
            className={input}
            value={f.minAmount}
            onChange={(e) => set('minAmount', e.target.value)}
            required
          />
        </div>
      )}

      {type === 'short_window_duplicate' && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label} htmlFor="f-hours">
              Window (hours)
            </label>
            <input
              id="f-hours"
              type="number"
              step="1"
              min="1"
              max="720"
              className={input}
              value={f.windowHours}
              onChange={(e) => set('windowHours', e.target.value)}
              required
            />
          </div>
          <div>
            <label className={label} htmlFor="f-tol">
              Amount tolerance (%)
            </label>
            <input
              id="f-tol"
              type="number"
              step="any"
              min="0"
              max="100"
              className={input}
              value={f.tolerance}
              onChange={(e) => set('tolerance', e.target.value)}
              required
            />
          </div>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving}
          className="text-xs px-3 py-1 rounded border border-[#262626] text-[#e5e5e5] hover:bg-[#1a1a1a] transition-colors disabled:opacity-30"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          disabled={saving}
          onClick={onCancel}
          className="text-xs px-3 py-1 rounded border border-[#262626] text-[#a3a3a3] hover:bg-[#1a1a1a] transition-colors disabled:opacity-30"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
