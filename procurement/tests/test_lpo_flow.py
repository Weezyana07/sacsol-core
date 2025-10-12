import pytest
from django.contrib.auth import get_user_model
from django.apps import apps

import io
from decimal import Decimal
from django.contrib.auth.models import User, Group
from django.urls import reverse
from rest_framework.test import APIClient
from procurement.models import Supplier, LPO, AuditLog
from inventory.models import InventoryEntry  
from datetime import date
import uuid

User = get_user_model()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="staff", email="staff@example.com", password="pass")


@pytest.fixture
def manager(db):
    mgr = User.objects.create_user(username="manager", email="mgr@example.com", password="pass")
    group, _ = Group.objects.get_or_create(name="manager")
    mgr.groups.add(group)
    return mgr


@pytest.fixture
def auth_staff(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c

@pytest.fixture
def auth_manager(manager):
    c = APIClient()
    c.force_authenticate(user=manager)
    return c


@pytest.fixture
def supplier_id(auth_staff):
    r = auth_staff.post(reverse("suppliers-list"), {"name": "BuildCo"}, format="json")
    return r.json()["id"]


pytestmark = pytest.mark.django_db

@pytest.fixture
def creator(api):
    u = User.objects.create_user(username="creator", password="pass")
    api.force_authenticate(user=u)
    return u

@pytest.fixture
def manager_user():
    g, _ = Group.objects.get_or_create(name="manager")
    m = User.objects.create_user(username="manager", password="pass")
    m.groups.add(g)
    return m

@pytest.fixture
def supplier():
    return Supplier.objects.create(supplier_code="SUP-2025-000010", name="Acme Vendor")

@pytest.fixture
def inv_item(db):
    return InventoryEntry.objects.create(
        date=date.today(),
        truck_registration=f"TST-{uuid.uuid4().hex[:6].upper()}",
        quantity=Decimal("0"),
        description="Widget A",
    )

def _create_lpo(api, supplier, inv_item):
    url = reverse("lpos-list")
    payload = {
        "supplier": supplier.id,
        "currency": "NGN",
        "delivery_address": "Ikeja",
        "tax_amount": "0.00",
        "discount_amount": "0.00",
        "items": [
            {"inventory_item": inv_item.id, "description": "Widget A", "qty": "10.00", "unit_price": "5.00"}
        ],
    }
    r = api.post(url, payload, format="json")
    assert r.status_code == 201, r.data
    return LPO.objects.get(id=r.data["id"])

def test_create_submit_approve_pdf(api, creator, manager_user, supplier, inv_item):
    lpo = _create_lpo(api, supplier, inv_item)
    assert lpo.subtotal == Decimal("50.00")
    assert lpo.grand_total == Decimal("50.00")

    # submit by creator
    submit_url = reverse("lpos-submit", args=[lpo.id])
    r1 = api.post(submit_url)
    assert r1.status_code == 200
    lpo.refresh_from_db()
    assert lpo.status == LPO.STATUS_SUBMITTED
    assert AuditLog.objects.filter(lpo=lpo, verb="submitted").exists()

    # approve by non-manager should fail
    approve_url = reverse("lpos-approve", args=[lpo.id])
    r2 = api.post(approve_url)
    assert r2.status_code == 403

    # approve by manager
    api.force_authenticate(user=manager_user)
    r3 = api.post(approve_url)
    assert r3.status_code == 200
    lpo.refresh_from_db()
    assert lpo.status == LPO.STATUS_APPROVED
    assert AuditLog.objects.filter(lpo=lpo, verb="approved").exists()

    # pdf
    pdf_url = reverse("lpos-pdf", args=[lpo.id])
    r4 = api.get(pdf_url)
    assert r4.status_code == 200
    assert r4["Content-Type"] == "application/pdf"

def test_soft_delete_guard(api, creator, supplier, inv_item):
    lpo = _create_lpo(api, supplier, inv_item)
    url = reverse("lpos-detail", args=[lpo.id])
    r = api.delete(url)
    assert r.status_code == 204
    lpo.refresh_from_db()
    assert lpo.deleted is True

@pytest.fixture
def make_inventory_item(db):
    def _mk(name="Widget", **overrides):
        payload = {
            "date": date.today(),
            "truck_registration": f"TST-{uuid.uuid4().hex[:6].upper()}",
            "quantity": Decimal("0"),
            "description": name,  # use a real text field on your model
        }
        payload.update(overrides)
        return InventoryEntry.objects.create(**payload)
    return _mk



@pytest.mark.django_db
def test_create_submit_approve_lpo_flow(auth_staff, auth_manager, supplier_id, make_inventory_item):
    item1 = make_inventory_item("Cement")
    item2 = make_inventory_item(name="Rod 12mm")

    # create LPO
    payload = {
        "supplier": supplier_id,
        "currency": "NGN",
        "tax_amount": "0.00",
        "discount_amount": "0.00",
        "items": [
            {"inventory_item": item1.id, "description": "25kg cement", "qty": "100.00", "unit_price": "7600.00"},
            {"inventory_item": item2.id, "description": "12mm rod", "qty": "50.00", "unit_price": "12000.00"},
        ],
    }
    r = auth_staff.post(reverse("lpos-list"), payload, format="json")
    assert r.status_code == 201, r.content
    lpo = r.json()
    assert lpo["lpo_number"].startswith("LPO-")
    assert lpo["subtotal"] and lpo["grand_total"]

    # submit
    r2 = auth_staff.post(reverse("lpos-submit", args=[lpo["id"]]))
    assert r2.status_code == 200
    assert r2.json()["status"] == "submitted"

    # approve: staff should be forbidden
    r_bad = auth_staff.post(reverse("lpos-approve", args=[lpo["id"]]))
    assert r_bad.status_code == 403

    # approve: manager succeeds
    r_ok = auth_manager.post(reverse("lpos-approve", args=[lpo["id"]]))
    assert r_ok.status_code == 200
    assert r_ok.json()["status"] == "approved"

    # pdf endpoint
    r_pdf = auth_staff.get(reverse("lpos-pdf", args=[lpo["id"]]))
    assert r_pdf.status_code == 200
    assert r_pdf["Content-Type"].startswith("application/pdf")
