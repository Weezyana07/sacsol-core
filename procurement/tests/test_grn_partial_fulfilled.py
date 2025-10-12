import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from django.apps import apps

from django.contrib.auth.models import User, Group
from django.urls import reverse
from procurement.models import Supplier, LPO, AuditLog, GoodsReceipt
from inventory.models import InventoryEntry

import datetime, uuid

User = get_user_model()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="staff", password="pass")


@pytest.fixture
def manager(db):
    u = User.objects.create_user(username="manager", password="pass")
    g, _ = Group.objects.get_or_create(name="manager")
    u.groups.add(g)
    return u


@pytest.fixture
def auth_staff(api, staff):
    api.force_authenticate(user=staff)
    return api


@pytest.fixture
def auth_manager(api, manager):
    api.force_authenticate(user=manager)
    return api


@pytest.fixture
def supplier_id(auth_staff):
    r = auth_staff.post(reverse("suppliers-list"), {"name": "GRN Co"}, format="json")
    return r.json()["id"]

pytestmark = pytest.mark.django_db


@pytest.fixture
def creator(api):
    u = User.objects.create_user(username="rec_creator", password="pass")
    api.force_authenticate(user=u)
    return u

@pytest.fixture
def manager_user():
    g, _ = Group.objects.get_or_create(name="manager")
    m = User.objects.create_user(username="rec_manager", password="pass")
    m.groups.add(g)
    return m

@pytest.fixture
def supplier():
    return Supplier.objects.create(supplier_code="SUP-2025-000030", name="Hardware Hub")

@pytest.fixture
def inv_item(make_inventory_item):
    return make_inventory_item("Bolt M8")

def _create_and_approve_lpo(api, manager_user, supplier, inv_item):
    # create
    r = api.post(reverse("lpos-list"), {
        "supplier": supplier.id,
        "currency": "NGN",
        "tax_amount": "0.00",
        "discount_amount": "0.00",
        "items": [{"inventory_item": inv_item.id, "description": "Bolt", "qty": "10.00", "unit_price": "1.50"}],
    }, format="json")
    assert r.status_code == 201, r.data
    lpo_id = r.data["id"]
    # submit + approve
    api.post(reverse("lpos-submit", args=[lpo_id]))
    api.force_authenticate(user=manager_user)
    api.post(reverse("lpos-approve", args=[lpo_id]))
    return lpo_id

def test_partial_then_fulfilled(api, creator, manager_user, supplier, inv_item):
    lpo_id = _create_and_approve_lpo(api, manager_user, supplier, inv_item)
    lpo_detail = reverse("lpos-detail", args=[lpo_id])

    # GRN 4
    r1 = api.post(reverse("grn-list"), {
        "lpo": lpo_id,
        "reference": "GRN-1",
        "items": [{"lpo_item": LPO.objects.get(pk=lpo_id).items.first().id, "qty_received": "4.00"}],
    }, format="json")
    assert r1.status_code == 201, r1.data
    lpo = LPO.objects.get(pk=lpo_id)
    assert lpo.status == LPO.STATUS_PARTIAL

    # inventory incremented to 4
    inv = InventoryEntry.objects.get(pk=lpo.items.first().inventory_item_id)
    inv.refresh_from_db()
    assert Decimal(inv.quantity) >= Decimal("4.00")

    # GRN remaining 6
    r2 = api.post(reverse("grn-list"), {
        "lpo": lpo_id,
        "reference": "GRN-2",
        "items": [{"lpo_item": lpo.items.first().id, "qty_received": "6.00"}],
    }, format="json")
    assert r2.status_code == 201, r2.data
    lpo.refresh_from_db()
    assert lpo.status == LPO.STATUS_FULFILLED
    assert AuditLog.objects.filter(lpo=lpo, verb="received").count() >= 2

def test_grn_cannot_exceed_remaining(api, creator, manager_user, supplier, inv_item):
    lpo_id = _create_and_approve_lpo(api, manager_user, supplier, inv_item)
    lpo = LPO.objects.get(pk=lpo_id)

    # First receive 9
    r1 = api.post(reverse("grn-list"), {
        "lpo": lpo_id,
        "items": [{"lpo_item": lpo.items.first().id, "qty_received": "9.00"}],
    }, format="json")
    assert r1.status_code == 201, r1.data

    # Then try 2 (exceeds remaining 1)
    r2 = api.post(reverse("grn-list"), {
        "lpo": lpo_id,
        "items": [{"lpo_item": lpo.items.first().id, "qty_received": "2.00"}],
    }, format="json")
    assert r2.status_code == 400

@pytest.fixture
def make_inventory_item(db):
    def _mk(name="Item", quantity=0):
        return InventoryEntry.objects.create(
            date=datetime.date.today(),
            truck_registration=f"TST-{uuid.uuid4().hex[:6].upper()}",
            quantity=Decimal(str(quantity)),
            description=name,        # store display name here
        )
    return _mk


@pytest.fixture
def approved_lpo(auth_staff, auth_manager, supplier_id, make_inventory_item):
    i1 = make_inventory_item("Widget A")
    i2 = make_inventory_item("Widget B")
    payload = {
        "supplier": supplier_id,
        "currency": "NGN",
        "tax_amount": "0.00",
        "discount_amount": "0.00",
        "items": [
            {"inventory_item": i1.id, "description": "A", "qty": "10.00", "unit_price": "100.00"},
            {"inventory_item": i2.id, "description": "B", "qty": "5.00", "unit_price": "200.00"},
        ],
    }
    r = auth_staff.post(reverse("lpos-list"), payload, format="json")
    lpo_id = r.json()["id"]
    auth_staff.post(reverse("lpos-submit", args=[lpo_id]))
    auth_manager.post(reverse("lpos-approve", args=[lpo_id]))
    return lpo_id


@pytest.mark.django_db
def test_partial_then_fulfilled_status_and_inventory(auth_staff, approved_lpo):
    # Fetch LPO details to find lpo_item IDs
    r = auth_staff.get(reverse("lpos-detail", args=[approved_lpo]))
    assert r.status_code == 200
    lpo = r.json()
    items = lpo["items"]
    a_id = items[0]["id"]  # qty 10.00
    b_id = items[1]["id"]  # qty 5.00

    # Partial receive (A=6, B=2) -> partially_received
    grn_url = reverse("grn-list")
    r1 = auth_staff.post(
        grn_url,
        {"lpo": approved_lpo, "items": [{"lpo_item": a_id, "qty_received": "6.00"}, {"lpo_item": b_id, "qty_received": "2.00"}]},
        format="json",
    )
    assert r1.status_code == 201, r1.content

    r_after = auth_staff.get(reverse("lpos-detail", args=[approved_lpo]))
    assert r_after.json()["status"] == "partially_received"

    # Over-receive guard (try exceed remaining for A: remaining 4, try 5)
    r_bad = auth_staff.post(
        grn_url,
        {"lpo": approved_lpo, "items": [{"lpo_item": a_id, "qty_received": "5.00"}]},
        format="json",
    )
    assert r_bad.status_code == 400

    # Receive remaining (A=4, B=3) -> fulfilled
    r2 = auth_staff.post(
        grn_url,
        {"lpo": approved_lpo, "items": [{"lpo_item": a_id, "qty_received": "4.00"}, {"lpo_item": b_id, "qty_received": "3.00"}]},
        format="json",
    )
    assert r2.status_code == 201

    r_done = auth_staff.get(reverse("lpos-detail", args=[approved_lpo]))
    assert r_done.json()["status"] == "fulfilled"
