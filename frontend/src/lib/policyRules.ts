import type { PolicyRule, RuleType } from '@/lib/api'

export const RULE_TYPE_LABELS: Record<RuleType, string> = {
  amount_limit: 'Amount limit',
  future_date: 'Future date',
  vendor_category: 'Allowed categories',
  round_number: 'Round number',
  short_window_duplicate: 'Duplicate in short window',
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function summarizeRule(rule: PolicyRule): string {
  const p = rule.parameters
  switch (rule.rule_type) {
    case 'amount_limit': {
      if (typeof p.limit !== 'number') return ''
      const cat = typeof p.category === 'string' && p.category ? titleCase(p.category) : ''
      return cat ? `${cat} ≤ $${p.limit}` : `Max $${p.limit}`
    }
    case 'round_number':
      return typeof p.min_amount === 'number' ? `Min $${p.min_amount}` : ''
    case 'short_window_duplicate':
      return typeof p.window_hours === 'number' && typeof p.amount_tolerance_pct === 'number'
        ? `${p.window_hours}h window, ${p.amount_tolerance_pct}% tolerance`
        : ''
    case 'vendor_category': {
      const cats = p.allowed_categories
      if (!cats || typeof cats !== 'object') return ''
      const n = Object.keys(cats).length
      return `${n} ${n === 1 ? 'category' : 'categories'}`
    }
    default:
      return ''
  }
}
