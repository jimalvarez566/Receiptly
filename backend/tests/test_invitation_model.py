import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models import Invitation, Tenant


async def _tenant(db_session):
    tenant = Tenant(name="Acme", created_by_user_id=uuid.uuid4())
    db_session.add(tenant)
    await db_session.flush()
    return tenant


def _invite(tenant_id, **kw):
    data = dict(
        tenant_id=tenant_id,
        role="member",
        token_hash="a" * 64,
        created_by_user_id=uuid.uuid4(),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    data.update(kw)
    return Invitation(**data)


def test_invite_ttl_default():
    assert settings.INVITE_TTL_DAYS == 7


async def test_invitation_roundtrip_and_defaults(db_session):
    tenant = await _tenant(db_session)
    db_session.add(_invite(tenant.id))
    await db_session.flush()
    inv = (await db_session.execute(select(Invitation))).scalars().one()
    assert inv.id is not None
    assert inv.accepted_at is None
    assert inv.accepted_by_user_id is None
    assert inv.revoked_at is None
    assert inv.created_at is not None


async def test_invitation_rejects_unknown_role(db_session):
    tenant = await _tenant(db_session)
    db_session.add(_invite(tenant.id, role="owner"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_token_hash_is_unique(db_session):
    tenant = await _tenant(db_session)
    db_session.add(_invite(tenant.id))
    await db_session.flush()
    db_session.add(_invite(tenant.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_deleting_tenant_cascades_invitations(db_session):
    tenant = await _tenant(db_session)
    db_session.add(_invite(tenant.id))
    await db_session.flush()
    await db_session.execute(delete(Tenant).where(Tenant.id == tenant.id))
    rows = (await db_session.execute(select(Invitation))).scalars().all()
    assert rows == []
