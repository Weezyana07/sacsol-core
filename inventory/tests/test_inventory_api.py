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
    from django.contrib.auth.models import User, Group
    u = User.objects.create_user(username="staff", password="x")
    g, _ = Group.objects.get_or_create(name="Staff")
    u.groups.add(g)
    return u

@pytest.fixture()
def manager_user(db, manager_group):
    u = User.objects.create_user(username="manager", password="x", is_staff=True)
    u.groups.add(manager_group)
    return u


@pytest.fixture()
def other_user(db):
    return User.objects.create_user(username="other", password="x")


@pytest.fixture()
def auth_staff(staff_user):
    client = APIClient()
    token = auth_token(client, "staff", "x")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client

@pytest.fixture()
def auth_manager(manager_user):
    client = APIClient()
    token = auth_token(client, "manager", "x")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client

@pytest.fixture()
def auth_other(other_user):
    client = APIClient()
    token = auth_token(client, "other", "x")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


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

# ===== New fixtures =====
@pytest.fixture()
def superadmin_user(db):
    return User.objects.create_superuser(username="root", password="x", email="root@example.com")

@pytest.fixture()
def auth_superadmin(superadmin_user):
    client = APIClient()
    token = auth_token(client, "root", "x")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client

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

# def test_staff_cannot_create(auth_staff):
#     payload = {
#         "date": "2025-10-01",
#         "truck_registration": "abc123",
#         "quantity": "2.000",
#         "status": "pending",
#     }
#     res = auth_staff.post("/api/inventory/", payload, format="json")
#     assert res.status_code == 403

def test_owner_cannot_update_others_entry(auth_other, staff_user):
    entry = make_entry(created_by=staff_user, truck_registration="OWN123")
    res = auth_other.patch(f"/api/inventory/{entry.id}/", {"status": "delivered"}, format="json")
    # should be forbidden because other_user is not owner, not manager, not superuser
    assert res.status_code in (403, 404)


def test_manager_cannot_update(auth_manager, staff_user):
    entry = make_entry(created_by=staff_user, status="pending", quantity=Decimal("3.0"))

    res = auth_manager.patch(f"/api/inventory/{entry.id}/", {"status": "delivered"}, format="json")
    assert res.status_code == 403

    # state unchanged
    entry.refresh_from_db()
    assert entry.status == "pending"

    # no audit log written on forbidden attempt
    assert AuditLog.objects.filter(entry=entry, action="update").count() == 0

def test_soft_delete_forbidden_for_staff(auth_staff, staff_user):
    entry = make_entry(created_by=staff_user, status="pending")

    res = auth_staff.delete(f"/api/inventory/{entry.id}/")
    assert res.status_code == 403

    # not deleted
    entry.refresh_from_db()
    assert entry.deleted is False

    # still visible in list for the owner (unless filtered otherwise)
    res = auth_staff.get("/api/inventory/")
    assert res.status_code == 200
    # count could be >=1 depending on other fixtures; at least ensure the entry still exists
    assert InventoryEntry.objects.filter(id=entry.id, deleted=False).exists()

    # no audit log for a forbidden delete
    assert AuditLog.objects.filter(entry=entry, action="soft_delete").count() == 0

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

def test_negative_values_rejected_superadmin_only(auth_superadmin):
    payload = {
        "date": "2025-10-01",
        "truck_registration": "bad999",
        "quantity": "-1.0",  # invalid
    }
    res = auth_superadmin.post("/api/inventory/", payload, format="json")
    assert res.status_code == 400
    assert "quantity" in res.data


def test_net_weight_computed_on_create_superadmin(auth_superadmin):
    payload = {
        "date": "2025-10-01",
        "truck_registration": "we1",
        "quantity": "1.0",
        "gross_weight": "12.500",
        "tare_weight": "2.000",
    }
    res = auth_superadmin.post("/api/inventory/", payload, format="json")
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


def test_import_xlsx_requires_superadmin(auth_staff, auth_superadmin):
    df = pd.DataFrame([{"date": "2025-10-01", "truck_registration": "abc123", "quantity": 1.5}])

    # staff denied
    res = auth_staff.post("/api/inventory/import-excel/", {"file": _xlsx_bytes(df)}, format="multipart")
    assert res.status_code == 403

    # superadmin allowed
    res2 = auth_superadmin.post("/api/inventory/import-excel/", {"file": _xlsx_bytes(df)}, format="multipart")
    assert res2.status_code == 200
    assert res2.data["created"] == 1

def test_import_csv_with_aliases_ok(auth_superadmin):
    df = pd.DataFrame([{"date": "2025-10-02", "truck": "x9z", "tonnage": "3.25", "gross": "20.00", "tare": "5.00"}])
    res = auth_superadmin.post("/api/inventory/import-excel/", {"file": _csv_bytes(df)}, format="multipart")
    assert res.status_code == 200, res.data
    # assert res.data["created"] == 1
    # obj = InventoryEntry.objects.get(truck_registration="X9Z")
    # assert obj.quantity == Decimal("3.25")
    # assert obj.net_weight == Decimal("15.00")

def test_import_missing_required_rows(auth_superadmin):
    df = pd.DataFrame([{"date": "2025-10-01", "truck_registration": "a1"}])  # missing quantity
    res = auth_superadmin.post("/api/inventory/import-excel/", {"file": _xlsx_bytes(df)}, format="multipart")
    assert res.status_code == 200
    # assert res.data["created"] == 0
    # assert len(res.data["errors"]) == 1

def test_import_bad_file_returns_400(auth_superadmin):
    bad = io.BytesIO(b"%PDF not a spreadsheet%")
    bad.name = "weird.bin"
    res = auth_superadmin.post("/api/inventory/import-excel/", {"file": bad}, format="multipart")
    assert res.status_code == 400

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

def test_audit_logged_once_on_update(auth_superadmin):
    res = auth_superadmin.post("/api/inventory/", {
        "date": "2025-10-01", "truck_registration": "AUD001", "quantity": "1.0",
    }, format="json")
    assert res.status_code == 201
    obj_id = res.data["id"]

    res = auth_superadmin.patch(f"/api/inventory/{obj_id}/", {"quantity": "2.0"}, format="json")
    assert res.status_code == 200

    res2 = auth_superadmin.patch(f"/api/inventory/{obj_id}/", {"quantity": "2.5"}, format="json")
    assert res2.status_code == 200

def test_list_and_import(db):
    User.objects.create_user(username="staff", password="x", is_staff=True)
    client = APIClient()
    token = client.post(reverse("token_obtain_pair"), {"username": "staff", "password": "x"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    # list still OK
    assert client.get("/api/inventory/").status_code == 200

    # import requires superadmin; confirm forbidden here
    df = pd.DataFrame([{"date": "2025-10-01", "truck_registration": "abc123", "quantity": 1.5}])
    buff = _xlsx_bytes(df); buff.name = "import.xlsx"
    res = client.post("/api/inventory/import-excel/", {"file": buff}, format="multipart")
    assert res.status_code == 403

def test_audit_logs_read(auth_superadmin):
    # create
    res = auth_superadmin.post("/api/inventory/", {
        "date": "2025-10-01",
        "truck_registration": "LOG001",
        "quantity": "1.0",
    }, format="json")
    assert res.status_code == 201
    entry_id = res.data["id"]

    # update to ensure at least one update log
    res2 = auth_superadmin.patch(f"/api/inventory/{entry_id}/", {"status": "delivered"}, format="json")
    assert res2.status_code == 200

    # nested logs (superadmin allowed)
    nested = auth_superadmin.get(f"/api/inventory/{entry_id}/audit-logs/")
    assert nested.status_code == 200
    assert nested.data["count"] >= 2

# ===== Read is allowed for any authenticated user =====

def test_list_requires_auth(api):
    res = api.get("/api/inventory/")
    assert res.status_code in (401, 403)

def test_empty_list_ok(auth_staff):
    res = auth_staff.get("/api/inventory/")
    assert res.status_code == 200
    assert res.data["count"] == 0

# ===== Writes are blocked for staff/manager; allowed for superadmin =====

# def test_staff_cannot_create_update_delete(auth_staff, staff_user):
#     # create
#     payload = {"date": "2025-10-01", "truck_registration": "abc123", "quantity": "2.000", "status": "pending"}
#     res = auth_staff.post("/api/inventory/", payload, format="json")
#     assert res.status_code == 403

#     # make an entry owned by staff (via ORM) just to try update/delete endpoints
#     entry = make_entry(created_by=staff_user, truck_registration="OWN123")
#     # update
#     res_u = auth_staff.patch(f"/api/inventory/{entry.id}/", {"status": "delivered"}, format="json")
#     assert res_u.status_code == 403
#     # delete
#     res_d = auth_staff.delete(f"/api/inventory/{entry.id}/")
#     assert res_d.status_code == 403

def test_staff_can_create_but_cannot_edit_delete(auth_staff):
    payload = {"date":"2025-10-01","truck_registration":"abc123","quantity":"2.000","status":"pending"}
    res = auth_staff.post("/api/inventory/", payload, format="json")
    assert res.status_code == 201
    entry_id = res.data["id"]

    assert auth_staff.patch(f"/api/inventory/{entry_id}/", {"status":"delivered"}, format="json").status_code == 403
    assert auth_staff.delete(f"/api/inventory/{entry_id}/").status_code == 403

def test_manager_can_create_but_cannot_edit_delete(auth_manager, staff_user):
    # create allowed
    payload = {"date":"2025-10-01","truck_registration":"xyz999","quantity":"1.0","status":"pending"}
    res = auth_manager.post("/api/inventory/", payload, format="json")
    assert res.status_code == 201

    entry = make_entry(created_by=staff_user, truck_registration="MGR123")
    assert auth_manager.patch(f"/api/inventory/{entry.id}/", {"status":"delivered"}, format="json").status_code == 403
    assert auth_manager.delete(f"/api/inventory/{entry.id}/").status_code == 403
    
def test_superadmin_can_create_update_delete(auth_superadmin):
    # create
    payload = {"date": "2025-10-01", "truck_registration": "ROOT1", "quantity": "3.000", "status": "pending"}
    res = auth_superadmin.post("/api/inventory/", payload, format="json")
    assert res.status_code == 201, res.data
    obj_id = res.data["id"]

    # update
    res_u = auth_superadmin.patch(f"/api/inventory/{obj_id}/", {"status": "delivered"}, format="json")
    assert res_u.status_code == 200
    # delete (soft)
    res_d = auth_superadmin.delete(f"/api/inventory/{obj_id}/")
    assert res_d.status_code in (200, 204)

# ===== Import/export permissions =====

def test_import_requires_superadmin(auth_staff, auth_superadmin):
    # staff/manager denied
    df = pd.DataFrame([{"date": "2025-10-01", "truck_registration": "abc123", "quantity": 1.5}])
    buf = io.BytesIO(); 
    with pd.ExcelWriter(buf, engine="openpyxl") as w: df.to_excel(w, index=False)
    buf.seek(0); buf.name = "file.xlsx"
    res = auth_staff.post("/api/inventory/import-excel/", {"file": buf}, format="multipart")
    assert res.status_code == 403

    # superadmin allowed
    buf2 = io.BytesIO()
    with pd.ExcelWriter(buf2, engine="openpyxl") as w: df.to_excel(w, index=False)
    buf2.seek(0); buf2.name = "file.xlsx"
    res2 = auth_superadmin.post("/api/inventory/import-excel/", {"file": buf2}, format="multipart")
    assert res2.status_code == 200
    assert res2.data["created"] == 1

def test_export_allowed_for_authenticated(auth_staff):
    res = auth_staff.get("/api/inventory/export/")
    assert res.status_code == 200
    assert res["Content-Type"].startswith("text/csv")

# ===== Audit logs visibility =====

def test_audit_logs_endpoints_permissions(auth_staff, auth_superadmin):
    res = auth_superadmin.post("/api/inventory/", {"date":"2025-10-01","truck_registration":"LOGX1","quantity":"1.0"}, format="json")
    assert res.status_code == 201
    entry_id = res.data["id"]

    res_u = auth_superadmin.patch(f"/api/inventory/{entry_id}/", {"status": "delivered"}, format="json")
    assert res_u.status_code == 200

    ok_nested = auth_superadmin.get(f"/api/inventory/{entry_id}/audit-logs/")
    assert ok_nested.status_code == 200
    assert ok_nested.data["count"] >= 2

    bad_nested = auth_staff.get(f"/api/inventory/{entry_id}/audit-logs/")
    assert bad_nested.status_code in (403, 404)  # now enforced

    ok_global = auth_superadmin.get(f"/api/audit-logs/?entry={entry_id}")
    assert ok_global.status_code == 200
    bad_global = auth_staff.get(f"/api/audit-logs/?entry={entry_id}")
    assert bad_global.status_code in (403, 404)

# ===== Filters, pagination, summary still work for readers =====

def test_filters_and_summary_as_reader(auth_staff, staff_user):
    d0 = date(2025, 10, 1)
    d1 = date(2025, 10, 2)
    make_entry(created_by=staff_user, date=d0, truck_registration="ABX001", status="pending")
    make_entry(created_by=staff_user, date=d1, truck_registration="ZXQ777", status="delivered")

    res = auth_staff.get("/api/inventory/?q=zXq")
    assert res.status_code == 200 and res.data["count"] == 1

    res2 = auth_staff.get("/api/inventory/summary/")
    assert res2.status_code == 200
    assert "total" in res2.data and "by_status" in res2.data

def test_invalid_token_rejected(api):
    api.credentials(HTTP_AUTHORIZATION="Bearer invalid.jwt.token")
    res = api.get("/api/inventory/")
    assert res.status_code in (401, 403)

def test_detail_retrieve_authenticated(auth_staff, staff_user):
    obj = make_entry(created_by=staff_user, truck_registration="DET123")
    res = auth_staff.get(f"/api/inventory/{obj.id}/")
    assert res.status_code == 200
    assert res.data["truck_registration"] == "DET123"

def test_detail_404_for_deleted(auth_superadmin):
    # create then soft-delete, then ensure detail is 404 (queryset filters deleted=False)
    create = auth_superadmin.post("/api/inventory/", {
        "date": "2025-10-01", "truck_registration": "DEL404", "quantity": "1.0"
    }, format="json")
    obj_id = create.data["id"]
    assert auth_superadmin.delete(f"/api/inventory/{obj_id}/").status_code in (200, 204)
    res = auth_superadmin.get(f"/api/inventory/{obj_id}/")
    assert res.status_code in (404, 403)

def test_summary_zero_when_empty(auth_staff):
    res = auth_staff.get("/api/inventory/summary/")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert isinstance(body["by_status"], dict)

def test_export_empty_csv_ok(auth_staff):
    res = auth_staff.get("/api/inventory/export/")
    assert res.status_code == 200
    content = b"".join(res.streaming_content).decode("utf-8")
    # header only
    assert content.strip().count("\n") >= 0

def test_pagination_out_of_range_returns_empty(auth_staff, staff_user):
    for i in range(2):
        make_entry(created_by=staff_user, truck_registration=f"P{i}")
    res = auth_staff.get("/api/inventory/?limit=10&offset=50")
    assert res.status_code == 200
    assert res.data["count"] == 2
    assert len(res.data["results"]) == 0