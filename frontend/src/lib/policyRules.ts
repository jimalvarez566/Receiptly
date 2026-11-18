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

export function defaultParameters(type: RuleType): Record<string, unknown> {
  switch (type) {
    case 'amount_limit':
      return { limit: 100 }
    case 'future_date':
      return {}
    case 'vendor_category':
      return { allowed_categories: { meals: ['restaurant'] } }
    case 'round_number':
      return { min_amount: 10 }
    case 'short_window_duplicate':
      return { window_hours: 48, amount_tolerance_pct: 10 }
  }
}

export function categoriesToText(c: Record<string, string[]>): string {
  return Object.entries(c)
    .map(([name, kws]) => `${name}: ${kws.join(', ')}`)
    .join('\n')
}

export function textToCategories(text: string): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    const idx = line.indexOf(':')
    const name = (idx === -1 ? line : line.slice(0, idx)).trim()
    const rest = idx === -1 ? '' : line.slice(idx + 1)
    out[name] = rest
      .split(',')
      .map((k) => k.trim())
      .filter(Boolean)
  }
  return out
}
