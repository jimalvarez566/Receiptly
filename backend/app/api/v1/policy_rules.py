from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, require_admin
from app.database import get_db
from app.models import PolicyRule
from app.schemas.policy_rule import (
    PolicyRuleCreate,
    PolicyRuleResponse,
    PolicyRuleUpdate,
)
from app.services.policy_rules import (
    KNOWN_RULE_TYPES,
    PolicyRuleError,
    validate_parameters,
)

router = APIRouter(prefix="/policy-rules", tags=["policy-rules"])


async def _get_rule(db: AsyncSession, context: RequestContext, rule_id: int) -> PolicyRule:
    result = await db.execute(
        select(PolicyRule).where(
            PolicyRule.id == rule_id, PolicyRule.tenant_id == context.tenant_id
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Policy rule not found")
    return rule


@router.get("", response_model=list[PolicyRuleResponse])
async def list_policy_rules(
    context: RequestContext = Depends(require_admin("view policies")),
    db: AsyncSession = Depends(get_db),
) -> list[PolicyRule]:
    result = await db.execute(
        select(PolicyRule)
        .where(PolicyRule.tenant_id == context.tenant_id)
        .order_by(PolicyRule.id)
    )
    return list(result.scalars().all())


@router.post("", response_model=PolicyRuleResponse, status_code=201)
async def create_policy_rule(
    body: PolicyRuleCreate,
    context: RequestContext = Depends(require_admin("edit policies")),
    db: AsyncSession = Depends(get_db),
) -> PolicyRule:
    try:
        parameters = validate_parameters(body.rule_type, body.parameters)
    except PolicyRuleError as err:
        raise HTTPException(status_code=400, detail=str(err))
    rule = PolicyRule(
        tenant_id=context.tenant_id,
        rule_name=body.rule_name,
        rule_type=body.rule_type,
        severity=body.severity,
        parameters=parameters,
        is_active=body.is_active,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


@router.patch("/{rule_id}", response_model=PolicyRuleResponse)
async def update_policy_rule(
    rule_id: int,
    body: PolicyRuleUpdate,
    context: RequestContext = Depends(require_admin("edit policies")),
    db: AsyncSession = Depends(get_db),
) -> PolicyRule:
    rule = await _get_rule(db, context, rule_id)
    if "parameters" in body.model_fields_set:
        if rule.rule_type not in KNOWN_RULE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Parameters can't be edited for unknown rule type '{rule.rule_type}'",
            )
        try:
            rule.parameters = validate_parameters(rule.rule_type, body.parameters)
        except PolicyRuleError as err:
            raise HTTPException(status_code=400, detail=str(err))
    if body.rule_name is not None:
        rule.rule_name = body.rule_name
    if body.severity is not None:
        rule.severity = body.severity
    if body.is_active is not None:
        rule.is_active = body.is_active
    await db.flush()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_policy_rule(
    rule_id: int,
    context: RequestContext = Depends(require_admin("edit policies")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    rule = await _get_rule(db, context, rule_id)
    await db.delete(rule)
    await db.flush()
    return Response(status_code=204)
