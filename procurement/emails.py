# procurement/emails.py
from __future__ import annotations
from django.core.mail import EmailMessage
from django.conf import settings

def send_lpo_pdf_to_supplier(*, supplier_email: str, subject: str, body: str, pdf_bytes: bytes, filename: str = "lpo.pdf") -> None:
    if not supplier_email:
        return
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        to=[supplier_email],
    )
    email.attach(filename, pdf_bytes, "application/pdf")
    email.send(fail_silently=True)  # tweak to False in prod if you want exceptions
