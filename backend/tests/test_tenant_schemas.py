from app.schemas.tenant import (
    MeResponse,
    MemberAddRequest,
    MembershipInfo,
    TenantCreate,
    TenantResponse,
)


def test_tenant_create_requires_name():
    obj = TenantCreate(name="Acme")
    assert obj.name == "Acme"


def test_member_add_request_normalises_email():
    obj = MemberAddRequest(email="Bob@Example.com")
    assert obj.email == "bob@example.com"


def test_me_response_shape():
    me = MeResponse(
        user_id="11111111-1111-1111-1111-111111111111",
        email="a@b.com",
        memberships=[MembershipInfo(tenant_id=1, name="Acme", role="owner")],
    )
    assert me.memberships[0].name == "Acme"


def test_tenant_response_from_attributes():
    class Row:
        id = 5
        name = "Acme"
        created_at = __import__("datetime").datetime(2026, 1, 1)

    out = TenantResponse.model_validate(Row())
    assert out.id == 5
