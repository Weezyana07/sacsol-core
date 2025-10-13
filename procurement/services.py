# procurement/services.py
from __future__ import annotations

import logging
import mimetypes
from base64 import b64encode
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LPO, LPOSequence

log = logging.getLogger(__name__)

# -------------------------
# Numbering helpers
# -------------------------
def _next_year_counter() -> Tuple[int, int]:
    """
    Returns (year, counter_after_increment) using a single global LPOSequence row per year.
    Safe under concurrency via select_for_update.
    """
    year = timezone.now().year
    with transaction.atomic():
        seq, _ = LPOSequence.objects.select_for_update().get_or_create(year=year)
        seq.counter += 1
        seq.save(update_fields=["counter"])
        return year, seq.counter


def next_lpo_number() -> str:
    """
    Generates a human LPO number like: 'LPO-2025-000123'.
    Prefix can be overridden via SACSOL_LPO_PREFIX setting/env.
    """
    prefix = getattr(settings, "SACSOL_LPO_PREFIX", "LPO")
    year, n = _next_year_counter()
    return f"{prefix}-{year}-{n:06d}"


def next_supplier_code() -> str:
    """
    Generates a human supplier code like: 'SUP-2025-000124'.
    Shares the yearly counter with LPOs (simple, single-tenant design).
    """
    prefix = getattr(settings, "SACSOL_SUPPLIER_PREFIX", "SUP")
    year, n = _next_year_counter()
    return f"{prefix}-{year}-{n:06d}"


# -------------------------
# Public helpers
# -------------------------
def public_verify_url(lpo: LPO) -> str:
    """
    Builds a public verify URL for the LPO. We try to use a base URL from settings.
    Fallback is a relative path that your frontend can handle.
    """
    base = getattr(settings, "SITE_URL", None) or getattr(settings, "FRONTEND_BASE_URL", "") or ""
    path = f"/verify/lpo/{lpo.lpo_number}"
    return f"{base.rstrip('/')}{path}" if base else path


def scan_bytes_for_malware(data: bytes) -> bool:
    """Stub: return True if clean, False (or raise) if infected."""
    return True


# -------------------------
# PDF rendering
# -------------------------
def _logo_src() -> str | None:
    """
    Return a data: URI for the company logo so WeasyPrint can always render it.
    If something goes wrong, log a warning and return None (logo omitted).
    """
    raw = getattr(settings, "COMPANY_LOGO_PATH", None)
    if not raw:
        log.warning("PDF: COMPANY_LOGO_PATH not set in settings.")
        return None

    p = Path(raw)
    if not p.is_file():
        log.warning("PDF: logo not found at %s", p.resolve())
        return None

    try:
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        data = p.read_bytes()
        return f"data:{mime};base64,{b64encode(data).decode('ascii')}"
    except Exception as e:
        log.error("PDF: failed reading logo at %s: %s", p, e)
        return None


def render_lpo_pdf_bytes(lpo: LPO) -> bytes:
    """
    Styled, print-ready LPO PDF (white background, professional layout).
    Uses WeasyPrint if available; otherwise returns a tiny valid PDF stub.
    """
    # Pull company meta safely
    C = {
        "name": getattr(settings, "COMPANY_NAME", "SACSOL ENGINEERING LIMITED"),
        "addr": getattr(settings, "COMPANY_ADDRESS", []),
        "phone": getattr(settings, "COMPANY_PHONE", ""),
        "email": getattr(settings, "COMPANY_EMAIL", ""),
        "rc": getattr(settings, "COMPANY_RC_NUMBER", ""),
        "tin": getattr(settings, "COMPANY_TAX_ID", ""),
        "logo": _logo_src(),
    }
    verify = public_verify_url(lpo)

    # Convenience formatters
    currency = getattr(lpo, "currency", "") or ""
    def fmt_money(x):
        try:
            return f"{currency} {float(x):,.2f}".strip()
        except Exception:
            return f"{currency} {x}".strip()

    def fmt_date(d):
        try:
            return d.strftime("%d %b %Y")
        except Exception:
            return str(d or "-")

    # Supplier bits (guard against None)
    sup = lpo.supplier
    sup_name = getattr(sup, "name", "") or "—"
    sup_addr = getattr(sup, "address", "") or ""
    sup_phone = getattr(sup, "phone", "") or ""
    sup_email = getattr(sup, "email", "") or ""

    # Build items rows once (description fallback)
    rows_html = "\n".join(
        f"""
        <tr>
          <td class="c">{i+1}</td>
          <td>{(it.description or getattr(it.inventory_item, "description", "") or str(it.inventory_item) or "").strip()}</td>
          <td class="r">{it.qty}</td>
          <td class="r">{fmt_money(it.unit_price)}</td>
          <td class="r">{fmt_money(it.line_total)}</td>
        </tr>
        """.strip()
        for i, it in enumerate(lpo.items.all())
    )

    # Inline CSS: explicit white, neutral borders; no CSS variables
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    @page {{
      size: A4;
      margin: 22mm 16mm 18mm 16mm;
    }}
    html, body {{
      background: #ffffff !important;
      color: #111827;
      font: 11pt -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Inter, Helvetica, Arial, sans-serif;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}
    .wrap {{ width: 100%; }}
    .header {{
      display: grid;
      grid-template-columns: 300px 1fr;
      gap: 14px;
      align-items: center;
      margin-bottom: 8px;
    }}
    .brand h1 {{
      margin: 0 0 2px 0;
      font-size: 14pt;
      letter-spacing: .5px;
    }}
    .brand small {{
      color: #6B7280;
      font-size: 9pt;
    }}
    .logo img {{
      max-height: 60px;
    }}
    .title {{
      margin-top: 10px;
      padding: 10px 12px;
      background: #F3F4F6;
      border: 1px solid #E5E7EB;
      border-radius: 8px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .6px;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: 1.1fr 1fr;
      gap: 12px;
      margin: 14px 0 10px;
    }}
    .box {{
      border: 1px solid #E5E7EB;
      border-radius: 8px;
      padding: 10px 12px;
      min-width: 0;
    }}
    .box h3 {{
      margin: 0 0 6px 0;
      font-size: 10pt;
      color: #6B7280;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .5px;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 6px 12px;
      font-size: 10.5pt;
    }}
    .kv div.label {{ color: #6B7280; }}
    table.items {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 8px;
      font-size: 10.5pt;
      min-width: 0;
    }}
    table.items th {{
      background: #F9FAFB;
      border: 1px solid #E5E7EB;
      padding: 7px 8px;
      text-align: left;
      font-weight: 600;
      color: #374151;
    }}
    table.items td {{
      border: 1px solid #E5E7EB;
      padding: 6px 8px;
      vertical-align: top;
    }}
    table.items td.c {{ text-align: center; width: 28px; }}
    table.items td.r {{ text-align: right; white-space: nowrap; }}
    .totals {{
      margin-top: 8px;
      display: grid;
      grid-template-columns: 1fr 240px;
      gap: 12px;
      align-items: start;
    }}
    .totalbox {{
      border: 1px solid #E5E7EB;
      border-radius: 8px;
      padding: 10px 12px;
    }}
    .totalbox .row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px 12px;
      font-size: 10.5pt;
      margin: 2px 0;
    }}
    .totalbox .row .label {{ color: #6B7280; }}
    .totalbox .grand {{ font-weight: 700; font-size: 11.5pt; margin-top: 6px; }}
    .terms {{
      margin-top: 12px;
      border: 1px solid #E5E7EB;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 10pt;
    }}
    .signs {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
    }}
    .sign {{
      border-top: 1px solid #9CA3AF;
      padding-top: 6px;
      font-size: 10pt;
      text-align: left;
    }}
    footer {{
      position: fixed;
      left: 0; right: 0; bottom: 10mm;
      text-align: center;
      color: #9CA3AF;
      font-size: 9pt;
    }}
    footer .page::after {{
      content: "Page " counter(page) " of " counter(pages);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="logo">
        {"<img src='" + (C["logo"] or "") + "' alt='Logo' />" if C["logo"] else ""}
      </div>
      <div class="brand">
        <h1>{C["name"]}</h1>
        <small>
          {" &middot; ".join([*C["addr"], C["phone"], C["email"]])}
          {" &middot; RC: " + C["rc"] if C["rc"] else ""}
          {" &middot; TIN: " + C["tin"] if C["tin"] else ""}
        </small>
      </div>
    </div>

    <div class="title">Local Purchase Order</div>

    <div class="meta-grid">
      <div class="box">
        <h3>Supplier</h3>
        <div style="font-weight:600; margin-bottom:4px;">{sup_name}</div>
        <div style="white-space:pre-line">{sup_addr}</div>
        <div style="margin-top:4px; color:#6B7280;">
          {sup_phone or ""}{(" &middot; " + sup_email) if (sup_phone and sup_email) else (sup_email or "")}
        </div>
      </div>

      <div class="box">
        <h3>Order Details</h3>
        <div class="kv">
          <div class="label">LPO No.</div><div>{lpo.lpo_number}</div>
          <div class="label">Status</div><div style="text-transform:capitalize">{lpo.status}</div>
          <div class="label">Created</div><div>{fmt_date(getattr(lpo, "created_at", None))}</div>
          <div class="label">Expected Delivery</div><div>{fmt_date(lpo.expected_delivery_date)}</div>
          <div class="label">Currency</div><div>{currency or "-"}</div>
          <div class="label">Payment Terms</div><div>{lpo.payment_terms or "-"}</div>
          <div class="label">Deliver To</div><div>{lpo.delivery_address or "-"}</div>
          <div class="label">Verify</div><div>{verify}</div>
        </div>
      </div>
    </div>

    <table class="items">
      <thead>
        <tr>
          <th class="c">#</th>
          <th>Description</th>
          <th class="r">Qty</th>
          <th class="r">Unit Price</th>
          <th class="r">Line Total</th>
        </tr>
      </thead>
      <tbody>
        {rows_html or "<tr><td class='c'>–</td><td>No items</td><td class='r'>–</td><td class='r'>–</td><td class='r'>–</td></tr>"}
      </tbody>
    </table>

    <div class="totals">
      <div></div>
      <div class="totalbox">
        <div class="row"><div class="label">Subtotal</div><div style="text-align:right">{fmt_money(lpo.subtotal)}</div></div>
        <div class="row"><div class="label">Tax</div><div style="text-align:right">{fmt_money(lpo.tax_amount)}</div></div>
        <div class="row"><div class="label">Discount</div><div style="text-align:right">{fmt_money(lpo.discount_amount)}</div></div>
        <div class="row grand"><div>Total</div><div style="text-align:right">{fmt_money(lpo.grand_total)}</div></div>
      </div>
    </div>

    <div class="terms">
      <h3 style="margin:0 0 6px 0; font-size:10pt; color:#6B7280; text-transform:uppercase; letter-spacing:.5px;">Terms &amp; Notes</h3>
      <ol style="margin:0; padding-left:16px; line-height:1.45;">
        <li>Supplier must quote LPO number on all invoices and delivery notes.</li>
        <li>Goods must match specified grade/specifications. Variations require written approval.</li>
        <li>Ownership transfers upon full delivery and acceptance at delivery location.</li>
        <li>Payment terms as stated above from date of invoice and receipt of complete documents.</li>
        <li>All disputes subject to applicable laws of Nigeria.</li>
      </ol>
    </div>

    <div class="signs">
      <div>
        <div style="height:38px"></div>
        <div class="sign">Authorized by: {getattr(getattr(lpo, "approved_by", None), "get_full_name", lambda: "")() or getattr(getattr(lpo, "approved_by", None), "username", "") or "____________________"}</div>
        <div style="font-size:9pt; color:#6B7280">For {C["name"]}</div>
      </div>
      <div>
        <div style="height:38px"></div>
        <div class="sign">Supplier’s Acknowledgement / Signature</div>
        <div style="font-size:9pt; color:#6B7280">{sup_name}</div>
      </div>
    </div>
  </div>

  <footer>
    <div class="page"></div>
    <div style="margin-top:4px;">This document was generated on {timezone.now().strftime("%d %b %Y, %H:%M")} — {verify}</div>
  </footer>
</body>
</html>
    """.strip()

    try:
        from weasyprint import HTML
        # Use filesystem base_url (better than HTTP) — mainly irrelevant since we embed the logo.
        base_url = Path(getattr(settings, "BASE_DIR", Path.cwd())).as_uri()
        return HTML(string=html, base_url=base_url).write_pdf()
    except Exception as e:
        log.error("PDF generation failed, returning stub: %s", e)
        # Minimal valid placeholder PDF bytes
        return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"