# procurement/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import LPO, AuditLog
from .services import render_lpo_pdf_bytes, public_verify_url

@receiver(post_save, sender=LPO)
def on_lpo_approved_email_supplier(sender, instance: LPO, created, **kwargs):
    if created or instance.status != LPO.STATUS_APPROVED:
        return
    update_fields = kwargs.get("update_fields")
    # only react when status/approved_at was just written
    if update_fields and not ({"status","approved_at"} & set(update_fields)):
        return
    # idempotency: if we've already emailed, skip
    if AuditLog.objects.filter(lpo=instance, verb="emailed").exists():
        return

    supplier = instance.supplier
    pdf = render_lpo_pdf_bytes(instance)
    subject = f"{instance.lpo_number} Approved"
    body = (
        f"Dear {supplier.name},\n\n"
        f"Please find attached Local Purchase Order {instance.lpo_number}.\n"
        f"You can also view/verify it at: {public_verify_url(instance)}\n\n"
        f"Regards,\nSacsol"
    )
    from .emails import send_lpo_pdf_to_supplier
    send_lpo_pdf_to_supplier(
        supplier_email=supplier.email,
        subject=subject,
        body=body,
        pdf_bytes=pdf,
        filename=f"{instance.lpo_number}.pdf",
    )
    AuditLog.objects.create(actor=None, verb="emailed", lpo=instance)