"""Immutable, recipient-safe offer documents and dependency-free PDF rendering."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from textwrap import wrap
from typing import Any, Mapping

from .money import from_minor, tax_minor
from .snapshots import validate_product_item_snapshot
from .tenancy.domain import SS_TENANT


SUPPORTED_LOCALES = frozenset({"de", "it", "en"})
RECIPIENT_FIELDS = (
    "name", "contactName", "email", "phone", "street", "houseNumber",
    "zip", "city", "country", "vatId",
)
ADDRESS_FIELDS = ("label", "street", "houseNumber", "zip", "city", "country")

LABELS = {
    "de": {
        "offer": "Angebot", "date": "Angebotsdatum", "valid": "Gültig bis",
        "provider": "Anbieter", "recipient": "Empfänger", "item": "Position",
        "qty": "Menge", "unit_price": "Einzelpreis", "net": "Netto",
        "tax": "Steuer", "gross": "Gesamt", "total_net": "Summe netto",
        "total_tax": "Steuer", "total_gross": "Gesamtsumme",
    },
    "it": {
        "offer": "Offerta", "date": "Data offerta", "valid": "Valida fino al",
        "provider": "Fornitore", "recipient": "Destinatario", "item": "Voce",
        "qty": "Quantità", "unit_price": "Prezzo unitario", "net": "Netto",
        "tax": "Imposta", "gross": "Totale", "total_net": "Totale netto",
        "total_tax": "Imposta", "total_gross": "Totale complessivo",
    },
    "en": {
        "offer": "Quote", "date": "Quote date", "valid": "Valid until",
        "provider": "Provider", "recipient": "Recipient", "item": "Item",
        "qty": "Quantity", "unit_price": "Unit price", "net": "Net",
        "tax": "Tax", "gross": "Total", "total_net": "Net total",
        "total_tax": "Tax", "total_gross": "Grand total",
    },
}


class OfferDocumentError(ValueError):
    pass


def _clean_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    cleaned = {
        field: deepcopy(value[field])
        for field in fields
        if isinstance(value.get(field), (str, int, float)) and str(value.get(field)).strip()
    }
    return cleaned or None


def build_offer_document_snapshot(
    offer: Mapping[str, Any],
    *,
    locale: str,
    finalized_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a public document exclusively from the offer's stored business snapshot."""

    if locale not in SUPPORTED_LOCALES:
        raise OfferDocumentError("Unsupported offer document locale")
    offer_id = offer.get("id")
    currency = offer.get("currency")
    created_at = offer.get("createdAt")
    if not isinstance(offer_id, str) or not offer_id or not isinstance(currency, str):
        raise OfferDocumentError("Offer has no reliable document identity")
    if not isinstance(created_at, (str, datetime)):
        raise OfferDocumentError("Offer has no reliable creation date")

    public_items: list[dict[str, Any]] = []
    total_net = 0
    total_tax = 0
    for source in offer.get("items") or []:
        try:
            validate_product_item_snapshot(source, currency=currency)
        except (TypeError, ValueError) as exc:
            raise OfferDocumentError("Offer has no reliable immutable item snapshot") from exc
        line_net = source["lineTotalMinor"]
        line_tax = tax_minor(line_net, source["taxRate"])
        public_items.append({
            "productName": source["productName"],
            "sku": source.get("sku") or "",
            "description": source.get("description") or "",
            "unit": source["unit"],
            "quantity": source["qty"],
            "unitPriceMinor": source["unitPriceMinor"],
            "discountMinor": source.get("discountMinor", 0),
            "taxRate": source["taxRate"],
            "netMinor": line_net,
            "taxMinor": line_tax,
            "grossMinor": line_net + line_tax,
        })
        total_net += line_net
        total_tax += line_tax
    if not public_items:
        raise OfferDocumentError("Offer has no document items")
    if offer.get("netTotalMinor") != total_net:
        raise OfferDocumentError("Offer total does not match immutable item snapshots")

    recipient = _clean_fields(offer.get("recipientSnapshot"), RECIPIENT_FIELDS)
    if recipient is None:
        recipient = _clean_fields(offer.get("companySnapshot"), RECIPIENT_FIELDS)
    if not recipient or not recipient.get("name"):
        raise OfferDocumentError("Offer has no reliable recipient snapshot")

    finalized = finalized_at or datetime.now(timezone.utc)
    return {
        "version": 1,
        "locale": locale,
        "finalizedAt": finalized.isoformat(),
        "offerNumber": offer_id,
        "offerDate": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
        "validUntil": (
            offer["validUntil"].isoformat() if isinstance(offer.get("validUntil"), datetime)
            else offer.get("validUntil") if isinstance(offer.get("validUntil"), str) else None
        ),
        "currency": currency,
        "provider": {
            "name": SS_TENANT.display_name,
            "legalName": SS_TENANT.legal_name,
        },
        "recipient": recipient,
        "billingAddress": _clean_fields(offer.get("billingAddressSnapshot"), ADDRESS_FIELDS),
        "deliveryAddress": _clean_fields(offer.get("deliveryAddressSnapshot"), ADDRESS_FIELDS),
        "items": public_items,
        "totals": {
            "netMinor": total_net,
            "taxMinor": total_tax,
            "grossMinor": total_net + total_tax,
        },
    }


def public_offer_payload(offer: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = offer.get("documentSnapshot")
    if not isinstance(snapshot, Mapping):
        raise OfferDocumentError("Offer document has not been finalized")
    return {
        "document": deepcopy(dict(snapshot)),
        "deliveryStatus": offer.get("deliveryStatus", "READY"),
        "responseStatus": offer.get("responseStatus", "OPEN"),
        "respondedAt": offer.get("respondedAt"),
    }


def _money(minor: int, currency: str) -> str:
    value = from_minor(minor)
    return f"{value:,.2f} {currency}".replace(",", " ")


def _address_lines(value: Mapping[str, Any] | None) -> list[str]:
    if not value:
        return []
    street = " ".join(str(value.get(key, "")).strip() for key in ("street", "houseNumber")).strip()
    city = " ".join(str(value.get(key, "")).strip() for key in ("zip", "city")).strip()
    return [line for line in (str(value.get("label", "")).strip(), street, city, str(value.get("country", "")).strip()) if line]


def _pdf_text(value: Any) -> bytes:
    text = str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return text.encode("cp1252", errors="replace")


def _render_page(lines: list[tuple[str, int, bool]]) -> bytes:
    commands = [b"BT", b"/F1 11 Tf", b"50 790 Td"]
    current_size = 11
    for text, size, bold in lines:
        if size != current_size or bold:
            commands.append(f"/{'F2' if bold else 'F1'} {size} Tf".encode())
            current_size = size
        commands.extend([b"0 -18 Td", b"(" + _pdf_text(text) + b") Tj"])
        if bold:
            commands.append(f"/F1 {size} Tf".encode())
    commands.append(b"ET")
    return b"\n".join(commands)


def render_offer_pdf(snapshot: Mapping[str, Any]) -> bytes:
    """Render a compact A4 PDF without introducing a second document service."""

    locale = snapshot.get("locale") if snapshot.get("locale") in SUPPORTED_LOCALES else "de"
    labels = LABELS[locale]
    currency = str(snapshot["currency"])
    provider = snapshot.get("provider") or {}
    recipient = snapshot.get("recipient") or {}
    lines: list[tuple[str, int, bool]] = [
        (f"{labels['offer']} {snapshot['offerNumber']}", 20, True),
        (f"{labels['date']}: {str(snapshot['offerDate'])[:10]}", 10, False),
    ]
    if snapshot.get("validUntil"):
        lines.append((f"{labels['valid']}: {str(snapshot['validUntil'])[:10]}", 10, False))
    lines.extend([
        ("", 10, False),
        (labels["provider"], 12, True),
        (provider.get("legalName") or provider.get("name") or "", 10, False),
        ("", 10, False),
        (labels["recipient"], 12, True),
        (recipient.get("name") or "", 10, False),
    ])
    if recipient.get("contactName"):
        lines.append((recipient["contactName"], 10, False))
    lines.extend((line, 10, False) for line in _address_lines(snapshot.get("billingAddress")))
    lines.extend([("", 10, False), (labels["item"], 12, True)])
    for index, item in enumerate(snapshot.get("items") or [], 1):
        title = f"{index}. {item['productName']}" + (f" ({item['sku']})" if item.get("sku") else "")
        for part in wrap(title, width=78) or [""]:
            lines.append((part, 10, False))
        lines.append((
            f"{labels['qty']}: {item['quantity']} {item['unit']}   "
            f"{labels['unit_price']}: {_money(item['unitPriceMinor'], currency)}   "
            f"{labels['net']}: {_money(item['netMinor'], currency)}   "
            f"{labels['tax']}: {item['taxRate']}%",
            9, False,
        ))
    totals = snapshot["totals"]
    lines.extend([
        ("", 10, False),
        (f"{labels['total_net']}: {_money(totals['netMinor'], currency)}", 11, True),
        (f"{labels['total_tax']}: {_money(totals['taxMinor'], currency)}", 11, False),
        (f"{labels['total_gross']}: {_money(totals['grossMinor'], currency)}", 13, True),
    ])

    chunks = [lines[index:index + 38] for index in range(0, len(lines), 38)]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages object filled after page/content object ids are known
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    page_ids: list[int] = []
    for chunk in chunks:
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        stream = _render_page(chunk)
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects[1] = (
        b"<< /Type /Pages /Count " + str(len(page_ids)).encode() + b" /Kids ["
        + b" ".join(f"{page_id} 0 R".encode() for page_id in page_ids) + b"] >>"
    )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)
