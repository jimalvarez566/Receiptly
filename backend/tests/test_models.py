import uuid

from sqlalchemy import select

from app.models import Employee, Membership, Receipt, Tenant


async def test_create_tenant_and_membership(db_session):
    uid = uuid.uuid4()
    tenant = Tenant(name="Acme Inc", created_by_user_id=uid)
    db_session.add(tenant)
    await db_session.flush()

    db_session.add(Membership(tenant_id=tenant.id, user_id=uid, role="owner"))
    await db_session.flush()

    rows = (await db_session.execute(select(Membership))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id == tenant.id
    assert rows[0].role == "owner"


async def test_receipt_requires_tenant_and_submitter(db_session):
    uid = uuid.uuid4()
    tenant = Tenant(name="Acme", created_by_user_id=uid)
    db_session.add(tenant)
    await db_session.flush()

    receipt = Receipt(
        image_path="acme/abc.png",
        tenant_id=tenant.id,
        submitted_by_user_id=uid,
        status="pending",
    )
    db_session.add(receipt)
    await db_session.flush()
    assert receipt.id is not None


async def test_employee_email_unique_per_tenant_only(db_session):
    uid = uuid.uuid4()
    t1 = Tenant(name="One", created_by_user_id=uid)
    t2 = Tenant(name="Two", created_by_user_id=uid)
    db_session.add_all([t1, t2])
    await db_session.flush()

    db_session.add(Employee(name="Bob", email="bob@x.com", tenant_id=t1.id))
    db_session.add(Employee(name="Bob", email="bob@x.com", tenant_id=t2.id))
    await db_session.flush()  # same email, different tenant — allowed
