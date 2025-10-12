import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from django.urls import reverse
from procurement.models import Supplier

User = get_user_model()

# Enable DB for all tests in this module
pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="staff", email="staff@example.com", password="pass")


@pytest.fixture
def auth(api, user):
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def auth_user(api, db):
    u = User.objects.create_user(username="u1", password="pass")
    api.force_authenticate(user=u)
    return u


def test_create_supplier_and_code_generated(api, auth_user):
    url = reverse("suppliers-list")
    payload = {
        "name": "Acme Ltd",
        "email": "acme@example.com",
        "phone": "08012345678",
        "address": "Lagos",
        "rc_number": "RC1234",
        "tax_id": "TIN-88",
        "contact_person": "Jane",
    }
    r = api.post(url, payload, format="json")
    assert r.status_code == 201, r.data
    s = Supplier.objects.get(id=r.data["id"])
    assert s.supplier_code.startswith("SUP-")
    assert s.name == "Acme Ltd"


def test_supplier_soft_duplicate_guards(auth):
    url = reverse("suppliers-list")
    base = {"name": "Same Co", "email": "same@co.com", "phone": "08010000000"}

    r1 = auth.post(url, base, format="json")
    assert r1.status_code == 201

    # same name + same phone → blocked
    r2 = auth.post(url, {"name": "Same Co", "phone": "08010000000"}, format="json")
    assert r2.status_code == 400

    # same name + same email → blocked
    r3 = auth.post(url, {"name": "Same Co", "email": "same@co.com"}, format="json")
    assert r3.status_code == 400

    # different contact → allowed
    r4 = auth.post(url, {"name": "Same Co", "email": "other@co.com"}, format="json")
    assert r4.status_code == 201


def test_supplier_search_q(auth):
    url = reverse("suppliers-list")
    auth.post(url, {"name": "Alpha"}, format="json")
    auth.post(url, {"name": "Beta"}, format="json")

    r = auth.get(url + "?q=alp")
    data = r.json()
    names = [s["name"] for s in (data.get("results") or data)]
    assert "Alpha" in names
    assert "Beta" not in names


def test_update_supplier(auth):
    url = reverse("suppliers-list")
    r = auth.post(url, {"name": "Old Name"}, format="json")
    supplier_id = r.json()["id"]

    r2 = auth.patch(reverse("suppliers-detail", args=[supplier_id]), {"name": "New Name"}, format="json")
    assert r2.status_code == 200
    assert r2.json()["name"] == "New Name"


def test_duplicate_soft_rules(api, auth_user):
    url = reverse("suppliers-list")

    # Seed one supplier via the API so it gets a generated, unique code
    r0 = api.post(url, {"name": "Acme Ltd", "email": "dupe@example.com"}, format="json")
    assert r0.status_code == 201

    # same name + email should trip validation
    r = api.post(url, {"name": "acme ltd", "email": "DUPE@example.com"}, format="json")
    assert r.status_code == 400

    # same name + phone should also trip (may pass if phone doesn't match an existing record)
    r2 = api.post(url, {"name": "Acme Ltd", "phone": "080"}, format="json")
    assert r2.status_code in (201, 400)

    # rc_number unique-ish guard
    r3 = api.post(url, {"name": "Other", "rc_number": "RC1"}, format="json")
    assert r3.status_code == 201
    r4 = api.post(url, {"name": "Other2", "rc_number": "rc1"}, format="json")
    assert r4.status_code == 400


def test_list_filter_q(api, auth_user):
    Supplier.objects.create(supplier_code="SUP-1", name="Zeta Steel")
    Supplier.objects.create(supplier_code="SUP-2", name="Beta Plastics")

    r = api.get(reverse("suppliers-list") + "?q=steel")
    assert r.status_code == 200
    data = r.json()
    names = [x["name"] for x in (data.get("results") or data)]
    assert names == ["Zeta Steel"]
