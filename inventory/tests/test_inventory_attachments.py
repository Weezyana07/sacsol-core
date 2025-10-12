# inventory/tests/test_inventory_attachments.py (or procurement/tests/...)
import io, pytest
from datetime import date
from PIL import Image
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from inventory.models import InventoryEntry, InventoryAttachment

pytestmark = pytest.mark.django_db

def _jpeg_file():
    im = Image.new("RGB", (300, 200), (120, 160, 200))
    buf = io.BytesIO(); im.save(buf, format="JPEG"); buf.seek(0)
    return SimpleUploadedFile("test.jpg", buf.read(), content_type="image/jpeg")

def test_inventory_attachment_upload_list_delete():
    api = APIClient()
    u = User.objects.create_user("u", password="p")
    api.force_authenticate(u)

    # ✅ required fields: date, truck_registration (quantity defaults to 0.0)
    e = InventoryEntry.objects.create(
        date=date.today(),
        truck_registration="ABC-123",
        quantity=0
    )

    # ✅ route names depend on router basename; see step 2
    url = reverse("inventoryentry-attachments", args=[e.id])
    r1 = api.post(url, {"file": _jpeg_file()}, format="multipart")
    assert r1.status_code == 201
    att_id = r1.data["id"]

    r2 = api.get(url)
    assert r2.status_code == 200
    assert len(r2.data) == 1

    del_url = reverse("inventoryentry-delete-attachment", args=[e.id, att_id])
    r3 = api.delete(del_url)
    assert r3.status_code == 204
    assert InventoryAttachment.objects.filter(entry=e).count() == 0
