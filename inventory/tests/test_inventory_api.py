import io
import csv
import json
import pandas as pd
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from inventory.models import InventoryEntry, AuditLog


@pytest.fixture()
def api():
    return APIClient()


@pytest.fixture()
def manager_group(db):
    g, _ = Group.objects.get_or_create(name="Manager")
    return g


def auth_token(client: APIClient, username: str, password: str) -> str:
    url = reverse("token_obtain_pair")
    res = client.post(url, {"username": username, "password": password}, format="json")
    assert res.status_code == 200, res.data
    return res.data["access"]


@pytest.fixture()
def staff_user(db):
    return User.objects.create_user(username="staff", password="x")  # not in Manager group


@pytest.fixture()
def manager_user(db, manager_group):
    u = User.objects.create_user(username="manager", password="x", is_staff=True)
    u.groups.add(manager_group)
    return u


@pytest.fixture()
def other_user(db):
    return User.objects.create_user(username="other", password="x")


@pytest.fixture()
def auth_staff(api, staff_user):
    token = auth_token(api, "staff", "x")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


@pytest.fixture()
def auth_manager(api, manager_user):
    token = auth_token(api, "manager", "x")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


@pytest.fixture()
def auth_other(api, other_user):
    token = auth_token(api, "other", "x")
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def make_entry(**kwargs) -> InventoryEntry:
    today = timezone.now().date()
    defaults = dict(
        date=today,
        truck_registration="ABC123",
        quantity=Decimal("1.500"),
        status="pending",
    )
    defaults.update(kwargs)
    return InventoryEntry.objects.create(**defaults)


# -----------------------------
# Auth & Basic list behavior
# -----------------------------

def test_list_requires_auth(api):
    res = api.get("/api/inventory/")
    assert res.status_code in (401, 403)  # permission posture requires auth for SAFE methods


def test_empty_list_ok(auth_staff):
    res = auth_staff.get("/api/inventory/")
    assert res.status_code == 200
    assert res.data["count"] == 0
    assert isinstance(res.data["results"], list)


# -----------------------------
# Create / Update / Permissions
# -----------------------------

def test_staff_can_create_own_entry(auth_staff, staff_user):
    payload = {
        "date": "2025-10-01",
        "truck_registration": "abc123",
        "quantity": "2.000",
        "status": "pending",
    }
    res = auth_staff.post("/api/inventory/", payload, format="json")
    assert res.status_code == 201, res.data

    obj = InventoryEntry.objects.get(id=res.data["id"])
    # uppercase enforced
    assert obj.truck_registration == "ABC123"
    # owner set
    assert obj.created_by_id == staff_user.id
    # audit created once
    logs = AuditLog.objects.filter(entry=obj, action="create")
    assert logs.count() == 1


def test_owner_cannot_update_others_entry(auth_other, staff_user):
    entry = make_entry(created_by=staff_user, truck_registration="OWN123")
    res = auth_other.patch(f"/api/inventory/{entry.id}/", {"status": "delivered"}, format="json")
    # should be forbidden because other_user is not owner, not manager, not superuser
    assert res.status_code in (403, 404)


def test_manager_can_update_any_entry(auth_manager, staff_user):
    entry = make_entry(created_by=staff_user, status="pending", quantity=Decimal("3.0"))
    res = auth_manager.patch(f"/api/inventory/{entry.id}/", {"status": "delivered"}, format="json")
    assert res.status_code == 200, res.data
    entry.refresh_from_db()
    assert entry.status == "delivered"
    # audit update recorded once
    logs = AuditLog.objects.filter(entry=entry, action="update")
    assert logs.count() == 1
    assert "status" in logs.first().changes


def test_soft_delete(auth_staff, staff_user):
    entry = make_entry(created_by=staff_user, status="pending")
    res = auth_staff.delete(f"/api/inventory/{entry.id}/")
    assert res.status_code in (204, 200)

    entry.refresh_from_db()
    assert entry.deleted is True
    # not listed anymore
    res = auth_staff.get("/api/inventory/")
    assert res.status_code == 200
    assert res.data["count"] == 0
    # audit delete recorded
    assert AuditLog.objects.filter(entry=entry, action="soft_delete").count() == 1


# -----------------------------
# Filters: q / status / from / to
# -----------------------------

def test_filter_q_status_date_range(auth_staff, staff_user):
    d0 = date(2025, 10, 1)
    d1 = date(2025, 10, 2)
    d2 = date(2025, 10, 3)

    make_entry(created_by=staff_user, date=d0, truck_registration="ABX001", status="pending")
    make_entry(created_by=staff_user, date=d1, truck_registration="ZXQ777", status="in_transit")
    make_entry(created_by=staff_user, date=d2, truck_registration="CAR555", status="delivered")
    make_entry(created_by=staff_user, date=d2, truck_registration="TRASH999", status="rejected")

    # q search hits truck_registration case-insensitive
    res = auth_staff.get("/api/inventory/?q=zXq")
    assert res.status_code == 200
    assert res.data["count"] == 1
    assert res.data["results"][0]["truck_registration"] == "ZXQ777"

    # status filter
    res = auth_staff.get("/api/inventory/?status=delivered")
    assert res.status_code == 200
    assert res.data["count"] == 1
    assert res.data["results"][0]["status"] == "delivered"

    # date range filter (inclusive)
    res = auth_staff.get("/api/inventory/?from=2025-10-02&to=2025-10-03")
    assert res.status_code == 200
    assert res.data["count"] == 3  # d1 + d2 entries


def test_pagination(auth_staff, staff_user):
    for i in range(3):
        make_entry(created_by=staff_user, truck_registration=f"A{i}")
    res = auth_staff.get("/api/inventory/?limit=1&offset=0")
    assert res.status_code == 200
    assert res.data["count"] == 3
    assert len(res.data["results"]) == 1
    res2 = auth_staff.get("/api/inventory/?limit=1&offset=1")
    assert res2.status_code == 200
    assert len(res2.data["results"]) == 1
    assert res.data["results"][0]["id"] != res2.data["results"][0]["id"]


# -----------------------------
# Summary totals
# -----------------------------

def test_summary_totals(auth_staff, staff_user):
    e1 = make_entry(created_by=staff_user, status="pending", quantity=Decimal("1.5"))
    e2 = make_entry(created_by=staff_user, status="delivered", quantity=Decimal("2.0"),
                    gross_weight=Decimal("30.250"), tare_weight=Decimal("10.000"))
    e2.refresh_from_db()  # net_weight auto-computed in clean/save
    assert e2.net_weight == Decimal("20.250")

    res = auth_staff.get("/api/inventory/summary/")
    assert res.status_code == 200, res.data
    body = res.json()
    assert body["total"] == 2
    assert body["by_status"]["pending"] == 1
    assert body["by_status"]["delivered"] == 1
    # aggregates present (may be strings or numbers depending on renderer)
    assert Decimal(str(body["total_quantity"])) == Decimal("3.5")
    assert Decimal(str(body["total_net_weight"])) == Decimal("20.250")


# -----------------------------
# Validation & computed fields
# -----------------------------

def test_negative_values_rejected(auth_staff):
    payload = {
        "date": "2025-10-01",
        "truck_registration": "bad999",
        "quantity": "-1.0",  # invalid
    }
    res = auth_staff.post("/api/inventory/", payload, format="json")
    assert res.status_code == 400
    assert "quantity" in res.data


def test_net_weight_computed_on_create(auth_staff):
    payload = {
        "date": "2025-10-01",
        "truck_registration": "we1",
        "quantity": "1.0",
        "gross_weight": "12.500",
        "tare_weight": "2.000",
    }
    res = auth_staff.post("/api/inventory/", payload, format="json")
    assert res.status_code == 201, res.data
    obj = InventoryEntry.objects.get(id=res.data["id"])
    assert obj.net_weight == Decimal("10.500")
    assert obj.truck_registration == "WE1"  # uppercased


# -----------------------------
# Import (xlsx / csv / aliases / errors)
# -----------------------------

def _xlsx_bytes(df: pd.DataFrame) -> io.BytesIO:
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    b.seek(0)
    return b


def _csv_bytes(df: pd.DataFrame) -> io.BytesIO:
    b = io.StringIO()
    df.to_csv(b, index=False)
    raw = io.BytesIO(b.getvalue().encode("utf-8"))
    raw.seek(0)
    return raw


def test_import_xlsx_minimal_ok(auth_staff):
    df = pd.DataFrame([{"date": "2025-10-01", "truck_registration": "abc123", "quantity": 1.5}])
    res = auth_staff.post("/api/inventory/import-excel/", {"file": _xlsx_bytes(df)}, format="multipart")
    assert res.status_code == 200, res.data
    assert res.data["created"] == 1
    assert InventoryEntry.objects.count() == 1
    assert InventoryEntry.objects.first().truck_registration == "ABC123"


def test_import_csv_with_aliases_ok(auth_staff):
    # Use aliases: "truck" and "tonnage"
    df = pd.DataFrame([{"date": "2025-10-02", "truck": "x9z", "tonnage": "3.25", "gross": "20.00", "tare": "5.00"}])
    res = auth_staff.post("/api/inventory/import-excel/", {"file": _csv_bytes(df)}, format="multipart")
    assert res.status_code == 200, res.data
    assert res.data["created"] == 1
    obj = InventoryEntry.objects.get(truck_registration="X9Z")
    assert obj.quantity == Decimal("3.25")
    assert obj.net_weight == Decimal("15.00")  # computed


def test_import_missing_required_rows(auth_staff):
    # Missing quantity
    df = pd.DataFrame([{"date": "2025-10-01", "truck_registration": "a1"}])
    res = auth_staff.post("/api/inventory/import-excel/", {"file": _xlsx_bytes(df)}, format="multipart")
    assert res.status_code == 200
    assert res.data["created"] == 0
    assert len(res.data["errors"]) == 1
    assert "missing required" in json.dumps(res.data["errors"]).lower()


def test_import_bad_file_returns_400(auth_staff):
    bad = io.BytesIO(b"%PDF not a spreadsheet%")
    bad.name = "weird.bin"
    res = auth_staff.post("/api/inventory/import-excel/", {"file": bad}, format="multipart")
    assert res.status_code == 400
    assert "error reading file" in json.dumps(res.data).lower()


# -----------------------------
# Export (CSV) honors filters
# -----------------------------

def test_export_csv_honors_filters(auth_staff, staff_user):
    make_entry(created_by=staff_user, date=date(2025, 10, 1), status="pending", truck_registration="EXP001")
    make_entry(created_by=staff_user, date=date(2025, 10, 2), status="delivered", truck_registration="EXP002")

    res = auth_staff.get("/api/inventory/export/?from=2025-10-02&to=2025-10-02")
    assert res.status_code == 200
    assert res["Content-Type"].startswith("text/csv")
    # Parse CSV to check row count (header + 1 row)
    content = b"".join(res.streaming_content).decode("utf-8")
    rows = list(csv.reader(io.StringIO(content)))
    assert len(rows) >= 2
    # first data row should be EXP002 somewhere in columns
    assert any("EXP002" in cell for cell in rows[1])


# -----------------------------
# Audit logging: no double logs
# -----------------------------

def test_audit_logged_once_on_update(auth_staff, staff_user):
    # create via API to trigger audit 'create'
    res = auth_staff.post("/api/inventory/", {
        "date": "2025-10-01",
        "truck_registration": "AUD001",
        "quantity": "1.0",
    }, format="json")
    assert res.status_code == 201
    obj_id = res.data["id"]
    obj = InventoryEntry.objects.get(id=obj_id)

    assert AuditLog.objects.filter(entry=obj, action="create").count() == 1

    # update once
    res = auth_staff.patch(f"/api/inventory/{obj_id}/", {"quantity": "2.0"}, format="json")
    assert res.status_code == 200
    assert AuditLog.objects.filter(entry=obj, action="update").count() == 1

    # update again
    res2 = auth_staff.patch(f"/api/inventory/{obj_id}/", {"quantity": "2.5"}, format="json")
    assert res2.status_code == 200
    assert AuditLog.objects.filter(entry=obj, action="update").count() == 2

def test_list_and_import(db):
    # create user and get JWT
    User.objects.create_user(username="staff", password="x", is_staff=True)
    client = APIClient()
    token = client.post(
        reverse("token_obtain_pair"),
        {"username": "staff", "password": "x"},
        format="json",
    ).data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    # empty list ok (auth required in our setup)
    res = client.get("/api/inventory/")
    assert res.status_code == 200

    # build a tiny Excel in memory
    df = pd.DataFrame([{"date": "2025-10-01", "truck_registration": "abc123", "quantity": 1.5}])
    buff = io.BytesIO()
    with pd.ExcelWriter(buff, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buff.seek(0)
    buff.name = "import.xlsx"  # 👈 important for multipart filename sniffing

    # import
    res = client.post("/api/inventory/import-excel/", {"file": buff}, format="multipart")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["created"] == 1

def test_audit_logs_read(auth_staff, staff_user):
    # create
    res = auth_staff.post("/api/inventory/", {
        "date": "2025-10-01",
        "truck_registration": "LOG001",
        "quantity": "1.0",
    }, format="json")
    assert res.status_code == 201
    entry_id = res.data["id"]

    # update to ensure at least one update log
    res2 = auth_staff.patch(f"/api/inventory/{entry_id}/", {"status": "delivered"}, format="json")
    assert res2.status_code == 200

    # nested logs
    nested = auth_staff.get(f"/api/inventory/{entry_id}/audit-logs/")
    assert nested.status_code == 200
    assert nested.data["count"] >= 2  # create + update

    # global logs filtered by entry
    global_logs = auth_staff.get(f"/api/audit-logs/?entry={entry_id}")
    assert global_logs.status_code == 200
    assert global_logs.data["count"] >= 2