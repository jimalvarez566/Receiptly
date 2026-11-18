# Company-Specific Policy Editing — Design

**Date:** 2026-11-18
**Branch:** `feature/policy-editing` (from `main` @ ba44394)
**Status:** Draft for approval

## Intent

Every business already has its own copy of the fraud policy rules (seeded from
defaults when the business is created), but nothing can change them. Let a
business's admins tune their own rules so the fraud checks match their real
expense policy.

## Decisions (from the user)

- **Admins** can edit a rule's limits, turn rules on and off, and add new rules.
- "Limits" means the amounts/settings **inside** the rules. There is no separate
  cap on number of rules or on how high a limit may be set.
- Admins can **delete** a rule as well as switch it off; **delete requires a
  confirmation** step.
- Admins only (uses the existing `require_admin` guard).

## Calls I made (flag any you disagree with)

1. "Add a new rule" means a new rule of one of the **existing rule types**
   (amount limit, future date, approved categories, round number, short-window
   duplicate). Admins cannot create new kinds of check; that needs code.
2. A rule's **type cannot be changed** after creation (delete and re-add).
3. **Members cannot see the rules at all** (403, "Only admins can view policies").
4. Changes apply to receipts analyzed **from then on**. Already-analyzed receipts
   keep their results until an admin hits "Re-analyze". Nothing runs
   automatically.
5. Deleting is safe for history: fraud flags store a copy of the rule's name and
   id in their `details`; there is no foreign key from flags to rules.
6. Each rule type validates its own settings and returns a readable 400 (bounds
   below are my choices). Unknown types (e.g. added by another branch, such as
   the anomaly detector) are listed and can be switched off or deleted, but
   their settings are not editable until a validator is registered.
7. No audit trail ("who changed what") in this piece. Possible follow-up.
8. No database migration: `policy_rules` already has every column needed.

## Goals / non-goals

**Goals:** admin-only API to list/create/edit/delete rules scoped to the
caller's business; per-type validation with clear messages; an admin-only
Policies page; tests that pin permissions, tenant isolation and validation.

**Non-goals:** new rule types or custom rule logic, caps on rule counts or
values, audit history, automatic re-analysis, per-category dashboards, member
read access.

## Backend

### Rule-type registry (`app/services/policy_rules.py`, new)
A registry maps each `rule_type` to a validator for its `parameters`; adding a
type later is one entry. Validators return the cleaned parameters or raise a
`ValueError` with a readable message (surfaced as 400).

| Type | `parameters` | Validation |
|---|---|---|
| `amount_limit` | `{limit: number, category?: string}` | `limit` > 0 and <= 1,000,000; `category` optional, 1-50 chars, lowercased |
| `future_date` | `{}` | must be empty |
| `vendor_category` | `{allowed_categories: {category: [keyword,...]}}` | 1+ categories; each has 1+ keywords; names/keywords non-empty strings, lowercased/trimmed, max 100 keywords per category |
| `round_number` | `{min_amount: number}` | > 0 and <= 1,000,000 |
| `short_window_duplicate` | `{window_hours: int, amount_tolerance_pct: number}` | `window_hours` 1-720; tolerance 0-100 |

Common fields: `rule_name` 1-100 chars (trimmed); `severity` in
`low|medium|high`; `is_active` bool.

### Endpoints (`app/api/v1/policy_rules.py`, new, prefix `/policy-rules`)
All four use `require_admin(...)`; all scope by `context.tenant_id`; a rule in
another business is a plain 404 "Policy rule not found".

| Endpoint | Behavior | Member message |
|---|---|---|
| `GET /policy-rules` | list the business's rules, ordered by id | "Only admins can view policies" |
| `POST /policy-rules` | body `{rule_name, rule_type, severity, parameters, is_active=true}`; 201 | "Only admins can edit policies" |
| `PATCH /policy-rules/{id}` | any of `rule_name`, `severity`, `parameters`, `is_active`; `rule_type` rejected (422); parameters validated against the stored type | same |
| `DELETE /policy-rules/{id}` | 204 | same |

Schemas in `app/schemas/policy_rule.py` (`PolicyRuleCreate`, `PolicyRuleUpdate`,
`PolicyRuleResponse`). Unknown `rule_type` on create -> 400 "Unknown rule type".
Register the router in `app/main.py` next to the others.

### What does not change
`policy_validator.py` already loads the business's active rules at analysis
time, so edits take effect on the next analysis with no pipeline change.
`seed_policies.py`/defaults unchanged.

## Frontend

- `api.ts`: `PolicyRule` type; `listPolicyRules`, `createPolicyRule`,
  `updatePolicyRule`, `deletePolicyRule`.
- New admin-only route `/policies` and sidebar link "Policies", hidden for
  members (use `useRole().isAdmin`; the route redirects members to `/`).
- Policies page: a list of rules (name, type label, severity, a current-limit
  summary); an on/off toggle per rule; "Edit" opens a type-specific form
  (limit; category; min amount; window and tolerance; category -> keywords as
  one line per category, comma-separated); "Add rule" picks a type then shows
  that form; "Delete" asks "Delete '<name>'? This can't be undone." with
  Cancel/Delete before calling the API.
- Server messages (validation, 403) shown inline via the existing `ApiError`.
- Short note on the page: "Changes apply to new receipts. Re-analyze older
  receipts to apply them."

## Tests
- Registry: valid and invalid parameters for each type (boundaries, wrong types,
  empty categories, `future_date` with params).
- API: admin list/create/patch/delete; member gets the exact 403 messages on all
  four; cross-business rule id -> 404; `rule_type` in PATCH rejected;
  patch parameters validated against the stored type; toggle off removes the
  rule from analysis (analyze a receipt before/after); delete keeps existing
  flags intact.
- Creating a business still seeds defaults (existing tests keep passing).
- Frontend: `npm run build`; lint adds no new errors. Manual check with the
  local end-to-end setup used for roles (admin vs member).

## Rollout
No migration, no config. Admins see the Policies page after deploy.
