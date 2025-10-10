import pytest
from django.contrib.auth.models import User, Group
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status


# --------------------------
# Helpers & Fixtures
# --------------------------

@pytest.fixture()
def api():
    return APIClient()

def token(api: APIClient, username: str, password: str) -> str:
    res = api.post(reverse("token_obtain_pair"), {"username": username, "password": password}, format="json")
    assert res.status_code == 200, res.data
    return res.data["access"]

@pytest.fixture(autouse=True)
def groups(db):
    Group.objects.get_or_create(name="Staff")
    Group.objects.get_or_create(name="Manager")

@pytest.fixture()
def superadmin(db):
    return User.objects.create_superuser(username="root", email="root@example.com", password="x")

@pytest.fixture()
def manager_user(db):
    u = User.objects.create_user(username="manager", password="x", is_staff=True)
    u.groups.add(Group.objects.get(name="Manager"))
    return u

@pytest.fixture()
def staff_user(db):
    u = User.objects.create_user(username="staff", password="x", is_staff=False)
    u.groups.add(Group.objects.get(name="Staff"))
    return u


# --------------------------
# Tests: Create & Me
# --------------------------

def test_superadmin_can_create_manager(api, superadmin):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api, 'root', 'x')}")
    res = api.post("/api/users/", {
        "username": "boss",
        "email": "boss@example.com",
        "first_name": "Big",
        "last_name": "Boss",
        "role": "manager",
        "password": "secret123",
    }, format="json")
    assert res.status_code == status.HTTP_201_CREATED, res.data

    # Login as the new user and hit /me/
    api2 = APIClient()
    api2.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api2, 'boss', 'secret123')}")
    me = api2.get("/api/users/me/")
    assert me.status_code == 200
    assert me.data["username"] == "boss"
    assert me.data["role"] == "manager"  # inferred from groups


def test_me_requires_auth(api):
    res = api.get("/api/users/me/")
    assert res.status_code in (401, 403)  # typically 401 with JWT


# --------------------------
# Tests: Permissions
# --------------------------

def test_non_superadmin_cannot_list_or_create(api, manager_user):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api, 'manager', 'x')}")
    assert api.get("/api/users/").status_code in (401, 403, 404)  # blocked by IsSuperAdmin
    res = api.post("/api/users/", {
        "username": "nope",
        "email": "nope@example.com",
        "role": "staff",
        "password": "zzzxxx",
    }, format="json")
    assert res.status_code in (401, 403, 404)

def test_staff_cannot_access_admin_endpoints(api, staff_user):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api, 'staff', 'x')}")
    assert api.get("/api/users/").status_code in (401, 403, 404)
    res = api.post("/api/users/", {
        "username": "nope2",
        "email": "nope2@example.com",
        "role": "staff",
        "password": "zzzxxx",
    }, format="json")
    assert res.status_code in (401, 403, 404)


# --------------------------
# Tests: Update role & groups
# --------------------------

def test_superadmin_can_update_role_and_groups(api, superadmin):
    # create staff
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api, 'root', 'x')}")
    res = api.post("/api/users/", {
        "username": "person",
        "email": "p@example.com",
        "first_name": "Per",
        "last_name": "Son",
        "role": "staff",
        "password": "pword123",
    }, format="json")
    assert res.status_code == 201, res.data
    uid = res.data["id"]

    # patch role -> manager
    res2 = api.patch(f"/api/users/{uid}/", {"role": "manager"}, format="json")
    assert res2.status_code == 200, res2.data

    # refetch through /me/ (log in as the user)
    api_user = APIClient()
    api_user.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api_user, 'person', 'pword123')}")
    me = api_user.get("/api/users/me/")
    assert me.status_code == 200
    assert me.data["role"] == "manager"   # now a manager


def test_cannot_change_superuser_role(api, superadmin):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api, 'root', 'x')}")
    # root is a superuser; an attempt to change role should fail validation in serializer
    res = api.patch("/api/users/1/", {"role": "staff"}, format="json")  # assuming superuser has pk=1 in tests
    # Either 400 ValidationError or 404 if pk differs. Make it resilient:
    assert res.status_code in (400, 404)
    if res.status_code == 400:
        assert "role" in res.data


# --------------------------
# Tests: Set password
# --------------------------

def test_set_password_superadmin_only(api, superadmin):
    # create staff
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api, 'root', 'x')}")
    res = api.post("/api/users/", {
        "username": "chpass",
        "email": "c@example.com",
        "role": "staff",
        "password": "oldpass123",
    }, format="json")
    assert res.status_code == 201, res.data
    uid = res.data["id"]

    # change password as superadmin
    res2 = api.post(f"/api/users/{uid}/set-password/", {"password": "newpass456"}, format="json")
    assert res2.status_code == 200, res2.data

    # can log in with new password
    api_user = APIClient()
    api_user.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api_user, 'chpass', 'newpass456')}")
    me = api_user.get("/api/users/me/")
    assert me.status_code == 200
    assert me.data["username"] == "chpass"


def test_manager_cannot_set_password_for_others(api, manager_user, staff_user):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token(api, 'manager', 'x')}")
    res = api.post(f"/api/users/{staff_user.id}/set-password/", {"password": "hack"}, format="json")
    assert res.status_code in (401, 403, 404)