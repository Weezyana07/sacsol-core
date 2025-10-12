# procurement/tests/test_attachment_upload.py
import io
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from inventory.models import InventoryEntry, InventoryAttachment


pytestmark = pytest.mark.django_db


# ---------- helpers ----------

def _jpeg_file(w=300, h=200, color=(120, 160, 200), name="img.jpg"):
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


def _png_file(w=300, h=200, color=(100, 120, 140), name="img.png"):
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/png")


def _pdf_file(size_kb=50, name="spec.pdf"):
    content = b"%PDF-1.4\n" + b"0" * (size_kb * 1024)  # fake but sized payload
    return SimpleUploadedFile(name, content, content_type="application/pdf")


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(api):
    u = User.objects.create_user("u", password="p")
    api.force_authenticate(u)
    return u


@pytest.fixture
def entry():
    return InventoryEntry.objects.create(
        date=date.today(),
        truck_registration="TEST-001",
        quantity=Decimal("0"),
        mineral_or_equipment="Paper A4",
        description="Paper A4",
    )


def attachments_url(eid):
    return reverse("inventoryentry-attachments", args=[eid])


def delete_attachment_url(eid, att_id):
    return reverse("inventoryentry-delete-attachment", args=[eid, att_id])


# ---------- tests ----------
# helper (place near your other helpers)
def _noisy_jpeg_file(side=2200, name="noisy.jpg"):
    """
    Create a high-entropy, large image that won't compress well, so it exceeds
    MAX_IMAGE_UPLOAD_KB even after the server resizes and recompresses it.
    """
    im = Image.effect_noise((side, side), 100.0).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90, optimize=False)  # big, noisy source
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


def test_upload_image_respects_size_cap_after_compression(api, user, entry, settings):
    # Make the post-compression size threshold tiny to trigger the guard
    settings.IMAGE_MAX_DIM = 2000
    settings.MAX_IMAGE_UPLOAD_KB = 5  # very small

    url = attachments_url(entry.id)
    # Use a large, noisy image so that even after resize/compression it's > 5 KB
    r = api.post(url, {"file": _noisy_jpeg_file()}, format="multipart")
    assert r.status_code == 400
    assert "Image too large after compression" in r.data["detail"]

def test_upload_image_list_and_dedupe(api, user, entry):
    url = attachments_url(entry.id)

    # First upload → 201
    r1 = api.post(url, {"file": _jpeg_file()}, format="multipart")
    assert r1.status_code == 201
    first_id = r1.data["id"]

    # List → one item
    r2 = api.get(url)
    assert r2.status_code == 200
    assert isinstance(r2.data, list)
    assert len(r2.data) == 1

    # Upload exact same bitmap again → de-dupe returns 200 with same record
    r3 = api.post(url, {"file": _jpeg_file()}, format="multipart")
    assert r3.status_code == 200
    assert r3.data["id"] == first_id

    # Delete
    del_url = delete_attachment_url(entry.id, first_id)
    r4 = api.delete(del_url)
    assert r4.status_code == 204
    assert InventoryAttachment.objects.filter(entry=entry).count() == 0


def test_upload_image_canonicalizes_to_jpeg(api, user, entry, settings):
    # Set generous caps so the test image isn't rejected
    settings.IMAGE_MAX_DIM = 2000
    settings.MAX_IMAGE_UPLOAD_KB = 500

    url = attachments_url(entry.id)

    # Upload PNG; server should convert to JPEG and set mime_type accordingly
    r = api.post(url, {"file": _png_file()}, format="multipart")
    assert r.status_code == 201
    att = r.data
    assert att["mime_type"] == "image/jpeg"
    assert att["width"] > 0 and att["height"] > 0
    # file field should now end with .jpg (server renames)
    assert str(att["file"]).lower().endswith(".jpg")


def test_upload_pdf_size_limit(api, user, entry, settings):
    settings.MAX_PDF_UPLOAD_MB = 1  # 1 MB
    url = attachments_url(entry.id)

    # Slightly over 1MB → expect 400
    too_big = _pdf_file(size_kb=1100)
    r = api.post(url, {"file": too_big}, format="multipart")
    assert r.status_code == 400
    assert "PDF too large" in r.data["detail"]

    # Under the limit → 201
    ok = _pdf_file(size_kb=500)
    r2 = api.post(url, {"file": ok}, format="multipart")
    assert r2.status_code == 201
    assert r2.data["mime_type"] == "application/pdf"


def test_upload_pdf_dedupes_by_checksum(api, user, entry, settings):
    settings.MAX_PDF_UPLOAD_MB = 2
    url = attachments_url(entry.id)

    pdf = _pdf_file(size_kb=256)
    r1 = api.post(url, {"file": pdf}, format="multipart")
    assert r1.status_code == 201
    att_id = r1.data["id"]

    # Re-upload same bytes → 200, same record
    pdf_dup = _pdf_file(size_kb=256)
    r2 = api.post(url, {"file": pdf_dup}, format="multipart")
    assert r2.status_code == 200
    assert r2.data["id"] == att_id

def test_upload_unsupported_type(api, user, entry):
    url = attachments_url(entry.id)
    bad = SimpleUploadedFile("evil.exe", b"MZ....", content_type="application/x-msdownload")
    r = api.post(url, {"file": bad}, format="multipart")
    assert r.status_code == 400
    assert "Unsupported file type" in r.data["detail"]
