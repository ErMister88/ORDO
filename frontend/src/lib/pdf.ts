import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { Platform } from "react-native";
import { euro, num, dateDE } from "@/src/lib/format";
import { COMPANY } from "@/src/legal";

const BRAND = "#0B1B3D";
const ACCENT = "#1D3B8E";

function wrap(title: string, inner: string): string {
  return `
  <html><head><meta charset="utf-8"/>
  <style>
    * { font-family: -apple-system, Helvetica, Arial, sans-serif; box-sizing: border-box; }
    body { margin: 0; padding: 40px; color: #0F172A; }
    .head { display:flex; justify-content:space-between; align-items:flex-start; border-bottom: 3px solid ${BRAND}; padding-bottom: 16px; margin-bottom: 24px; }
    .logo { font-size: 22px; font-weight: 800; color: ${BRAND}; }
    .logo small { display:block; font-size: 11px; font-weight:600; color:#64748B; letter-spacing:1px; }
    h1 { font-size: 20px; margin: 0 0 4px; }
    .muted { color:#64748B; font-size: 12px; }
    table { width:100%; border-collapse: collapse; margin: 20px 0; }
    th { text-align:left; font-size: 11px; text-transform:uppercase; color:#64748B; border-bottom:1px solid #E2E8F0; padding: 8px 6px; }
    td { padding: 10px 6px; border-bottom:1px solid #F1F5F9; font-size: 13px; }
    .right { text-align:right; }
    .total { font-size: 18px; font-weight: 800; color: ${BRAND}; }
    .box { background:#F8FAFC; border:1px solid #E2E8F0; border-radius:12px; padding:16px; margin-bottom:16px; }
    .badge { display:inline-block; background:${ACCENT}; color:#fff; padding:3px 10px; border-radius:999px; font-size:11px; font-weight:700; }
    .foot { margin-top:40px; padding-top:16px; border-top:1px solid #E2E8F0; font-size:11px; color:#94A3B8; }
  </style></head>
  <body>
    <div class="head">
      <div class="logo">S&amp;S Großhandel<small>B2B VERTRIEBSPORTAL</small></div>
      <div class="muted">${dateDE(new Date().toISOString())}</div>
    </div>
    ${inner}
    <div class="foot">S&amp;S Großhandel GmbH · Nürnberg · Dieses Dokument wurde automatisch erzeugt.</div>
  </body></html>`;
}

async function shareHtml(html: string, filename: string) {
  try {
    if (Platform.OS === "web") {
      // printToFileAsync is not implemented on web — use the browser print dialog
      await Print.printAsync({ html });
      return;
    }
    const { uri } = await Print.printToFileAsync({ html });
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(uri, { mimeType: "application/pdf", dialogTitle: filename, UTI: "com.adobe.pdf" });
    }
  } catch (e) {
    console.warn("PDF konnte nicht erstellt werden", e);
  }
}

export function glsTrackUrl(tracking?: string, zip?: string): string {
  const t = (tracking || "").trim();
  if (!t) return "";
  const z = (zip || "").trim();
  return z
    ? `https://gls-group.eu/track/${t}/postalcode/${z}`
    : `https://gls-group.eu/DE/de/paketverfolgung?match=${t}`;
}

export async function shareShopInvoicePdf(order: any) {
  const c = order.customer || {};
  const rows = (order.items || [])
    .map((i: any) => {
      const gross = i.price * i.qty;
      const rate = i.taxRate ?? 7;
      const net = gross / (1 + rate / 100);
      return `<tr><td>${i.name ?? i.productId}</td>
        <td class="right">${num(i.qty)}</td>
        <td class="right">${euro(i.price)}</td>
        <td class="right">${rate}%</td>
        <td class="right">${euro(net)}</td>
        <td class="right">${euro(gross)}</td></tr>`;
    })
    .join("");
  const vat = taxSummary(order.taxBreakdown);
  const paid = order.paymentStatus === "Bezahlt";
  const inner = `
    <h1>Rechnung ${order.id}</h1>
    <span class="badge">${paid ? "Bezahlt" : "Offen"}</span>
    <div class="box" style="margin-top:16px;">
      <strong>Rechnungsempfänger</strong><br/>
      <span class="muted">${c.name ?? ""}<br/>${c.street ?? ""}<br/>${c.zip ?? ""} ${c.city ?? ""}${c.email ? `<br/>${c.email}` : ""}</span>
    </div>
    <table>
      <tr><th>Position</th><th class="right">Menge</th><th class="right">Einzelpreis</th><th class="right">MwSt</th><th class="right">Netto</th><th class="right">Brutto</th></tr>
      ${rows}
    </table>
    ${order.discount > 0 ? `<div class="right muted">Rabatt${order.discountPercent ? ` (${order.discountPercent}%)` : ""}: -${euro(order.discount)}</div>` : ""}
    <div class="right muted">Versand: ${order.shipping === 0 ? "Gratis" : euro(order.shipping)}</div>
    ${vat}
    <div class="right total" style="margin-top:6px;">Gesamt (brutto): ${euro(order.total)}</div>
    <p class="muted">Bestelldatum: ${dateDE(order.createdAt)} · Zahlungsstatus: ${order.paymentStatus ?? "Offen"} · Lieferstatus: ${order.status ?? "Neu"}</p>
    ${order.trackingNumber ? `<p class="muted">Sendungsnummer (GLS): ${order.trackingNumber}</p>` : ""}`;
  const html = shopWrap(`Rechnung ${order.id}`, inner);
  await shareHtml(html, `Rechnung-${order.id}.pdf`);
}

const MTYPE: Record<string, string> = { kauf: "Kauf", finanzierung: "Finanzierung", leasing: "Leasing (Kaffeebindung)" };

export async function shareMachineInvoicePdf(req: any) {
  const c = req.customer || {};
  const gross = Number(req.machinePrice || 0);
  const net = gross / 1.19;
  const vat = gross - net;
  const paid = req.paymentStatus === "Bezahlt";
  const inner = `
    <h1>Kaufbeleg ${req.id}</h1>
    <span class="badge">${paid ? "Bezahlt" : "Offen"}</span>
    <div class="box" style="margin-top:16px;">
      <strong>Rechnungsempfänger</strong><br/>
      <span class="muted">${c.companyName || c.userName || ""}${c.userName && c.companyName ? `<br/>${c.userName}` : ""}${c.email ? `<br/>${c.email}` : ""}</span>
    </div>
    <table>
      <tr><th>Position</th><th class="right">MwSt</th><th class="right">Netto</th><th class="right">Brutto</th></tr>
      <tr><td>${req.machineName}</td><td class="right">19%</td><td class="right">${euro(net)}</td><td class="right">${euro(gross)}</td></tr>
    </table>
    <div class="right muted">Netto: ${euro(net)}</div>
    <div class="right muted">MwSt 19%: ${euro(vat)}</div>
    <div class="right total" style="margin-top:6px;">Gesamt (brutto): ${euro(gross)}</div>
    <p class="muted">Belegdatum: ${dateDE(req.paidAt || req.createdAt)} · Zahlungsstatus: ${req.paymentStatus || "Offen"}</p>`;
  await shareHtml(shopWrap(`Kaufbeleg ${req.id}`, inner), `Kaufbeleg-${req.id}.pdf`);
}

export async function shareMachineContractPdf(req: any) {
  const c = req.customer || {};
  const t = req.terms || {};
  const rows: string[] = [];
  const add = (label: string, value: string) => rows.push(`<tr><td>${label}</td><td class="right">${value}</td></tr>`);
  add("Maschine", req.machineName);
  add("Vertragsart", MTYPE[req.type] || req.type);
  add("Kaufpreis (Brutto, inkl. 19% MwSt)", euro(req.machinePrice));
  if (t.downPayment != null) add("Anzahlung", euro(t.downPayment));
  if (t.monthlyRate != null) add("Monatliche Rate", euro(t.monthlyRate));
  if (t.termMonths != null) add("Laufzeit", `${t.termMonths} Monate`);
  if (t.finalPayment != null) add("Schlussrate (Übernahme)", euro(t.finalPayment));
  if (t.coffeeName) add("Kaffeesorte", t.coffeeName);
  if (t.coffeePricePerKg != null) add("Kaffeepreis", `${euro(t.coffeePricePerKg)} / kg`);
  if (t.minCoffeeKgMonth != null) add("Kaffee-Mindestabnahme", `${t.minCoffeeKgMonth} kg / Monat`);
  const inner = `
    <h1>${MTYPE[req.type] || "Vertrag"} ${req.id}</h1>
    <span class="badge">${req.status}</span>
    <div class="box" style="margin-top:16px;">
      <strong>Vertragspartner</strong><br/>
      <span class="muted">${c.companyName || c.userName || ""}${c.userName && c.companyName ? `<br/>${c.userName}` : ""}${c.email ? `<br/>${c.email}` : ""}</span>
    </div>
    <table>
      <tr><th>Konditionen</th><th class="right">Wert</th></tr>
      ${rows.join("")}
    </table>
    ${t.note ? `<p class="muted">Hinweis: ${t.note}</p>` : ""}
    ${req.type === "leasing" ? `<p class="muted">Die Kaffeebindung wird als separater Kaffeeliefervertrag geführt. Die Maschine kann am Laufzeitende gegen Zahlung der Schlussrate übernommen werden.</p>` : ""}
    <p class="muted">Vertragsdatum: ${dateDE(req.createdAt)}</p>
    <div style="margin-top:40px;display:flex;justify-content:space-between;">
      <div style="border-top:1px solid #94A3B8;width:45%;text-align:center;padding-top:6px;font-size:11px;color:#64748B;">Ort, Datum, Unterschrift Kunde</div>
      <div style="border-top:1px solid #94A3B8;width:45%;text-align:center;padding-top:6px;font-size:11px;color:#64748B;">${COMPANY.name}</div>
    </div>`;
  await shareHtml(shopWrap(`${MTYPE[req.type] || "Vertrag"} ${req.id}`, inner), `Vertrag-${req.id}.pdf`);
}

function shopWrap(title: string, inner: string): string {
  return wrap(title, inner)
    .replace(
      "S&amp;S Großhandel<small>B2B VERTRIEBSPORTAL</small>",
      "S&amp;S coffee and more<small>KAFFEE-SHOP</small>",
    )
    .replace(
      "S&amp;S Großhandel GmbH · Nürnberg · Dieses Dokument wurde automatisch erzeugt.",
      `${COMPANY.name} · ${COMPANY.street}, ${COMPANY.zip} ${COMPANY.city} · USt-IdNr. ${COMPANY.vatId} · Dieses Dokument wurde automatisch erzeugt.`,
    );
}

export async function shareOfferPdf(offer: any, company: any, products: Record<string, any>) {
  const rows = offer.items
    .map((i: any) => {
      const p = products[i.productId];
      const line = i.price * i.qty;
      return `<tr><td>${p ? `${p.brand} ${p.name}` : i.productId}</td>
        <td class="right">${num(i.qty)} kg</td>
        <td class="right">${euro(i.price)}</td>
        <td class="right">${euro(line)}</td></tr>`;
    })
    .join("");
  const total = offer.items.reduce((a: number, i: any) => a + i.price * i.qty, 0);
  const inner = `
    <h1>Angebot ${offer.id}</h1>
    <span class="badge">${offer.status}</span>
    <div class="box" style="margin-top:16px;">
      <strong>${company?.name ?? ""}</strong><br/>
      <span class="muted">${company?.city ?? ""} · USt-ID: ${company?.vatId ?? "-"}</span>
    </div>
    <table>
      <tr><th>Produkt</th><th class="right">Menge/Monat</th><th class="right">Preis/kg</th><th class="right">Summe/Monat</th></tr>
      ${rows}
    </table>
    <div class="right total">Monatlich: ${euro(total)}</div>
    <p class="muted">Laufzeit: ${offer.termMonths} Monate${offer.reason ? ` · Begründung: ${offer.reason}` : ""}</p>`;
  await shareHtml(wrap(`Angebot ${offer.id}`, inner), `Angebot-${offer.id}.pdf`);
}

function vatRows(lineItems: any[]): string {
  return lineItems
    .map(
      (i: any) =>
        `<tr><td>${i.name ?? i.productId}</td>
        <td class="right">${num(i.qty)} ${i.unit ?? "kg"}</td>
        <td class="right">${euro(i.price)}</td>
        <td class="right">${i.taxRate ?? 7}%</td>
        <td class="right">${euro(i.net ?? i.price * i.qty)}</td></tr>`,
    )
    .join("");
}

function taxSummary(breakdown: Record<string, number>): string {
  return Object.entries(breakdown || {})
    .map(([rate, amt]) => `<div class="right muted">zzgl. ${rate}% MwSt: ${euro(amt as number)}</div>`)
    .join("");
}

export async function shareInvoicePdf(invoice: any, company: any) {
  if (invoice.lineItems && invoice.lineItems.length) {
    const inner = `
      <h1>Rechnung ${invoice.id}</h1>
      <span class="badge">${invoice.status}</span>
      <div class="box" style="margin-top:16px;">
        <strong>${company?.name ?? "Kunde"}</strong><br/>
        <span class="muted">${company?.city ?? ""} · USt-ID: ${company?.vatId ?? "-"}</span>
      </div>
      <table>
        <tr><th>Position</th><th class="right">Menge</th><th class="right">Preis</th><th class="right">MwSt</th><th class="right">Netto</th></tr>
        ${vatRows(invoice.lineItems)}
      </table>
      <div class="right muted">Nettobetrag: ${euro(invoice.net ?? invoice.amount)}</div>
      ${taxSummary(invoice.taxBreakdown)}
      <div class="right total" style="margin-top:6px;">Gesamt (brutto): ${euro(invoice.amount)}</div>
      <p class="muted">Rechnungsdatum: ${dateDE(invoice.date)} · Status: ${invoice.status}</p>`;
    await shareHtml(wrap(`Rechnung ${invoice.id}`, inner), `Rechnung-${invoice.id}.pdf`);
    return;
  }
  const inner = `
    <h1>Rechnung ${invoice.id}</h1>
    <span class="badge">${invoice.status}</span>
    <div class="box" style="margin-top:16px;">
      <strong>${company?.name ?? "Kunde"}</strong><br/>
      <span class="muted">${company?.city ?? ""} · USt-ID: ${company?.vatId ?? "-"}</span>
    </div>
    <table>
      <tr><th>Beschreibung</th><th class="right">Betrag</th></tr>
      <tr><td>Rechnung vom ${dateDE(invoice.date)}</td><td class="right">${euro(invoice.amount)}</td></tr>
    </table>
    <div class="right total">Gesamt: ${euro(invoice.amount)}</div>
    <p class="muted">Status: ${invoice.status}</p>`;
  await shareHtml(wrap(`Rechnung ${invoice.id}`, inner), `Rechnung-${invoice.id}.pdf`);
}

export async function shareDeliveryNotePdf(order: any, company: any, products: Record<string, any>) {
  const rows = order.items
    .map((i: any) => {
      const p = products[i.productId];
      return `<tr><td>${p ? `${p.brand} ${p.name}` : i.productId}</td>
        <td class="right">${num(i.qty)} ${p?.unit ?? "kg"}</td></tr>`;
    })
    .join("");
  const inner = `
    <h1>Lieferschein zu ${order.id}</h1>
    <span class="badge">${order.status}</span>
    <div class="box" style="margin-top:16px;">
      <strong>${company?.name ?? "Kunde"}</strong><br/>
      <span class="muted">${company?.city ?? ""}${company?.phone ? ` · ${company.phone}` : ""}</span>
    </div>
    <table>
      <tr><th>Artikel</th><th class="right">Menge</th></tr>
      ${rows}
    </table>
    ${order.trackingNumber ? `<p class="muted">Sendungsnummer: ${order.trackingNumber}</p>` : ""}
    ${order.estimatedDelivery ? `<p class="muted">Voraussichtliche Lieferung: ${dateDE(order.estimatedDelivery)}</p>` : ""}
    <p class="muted">Bitte prüfen Sie die Ware bei Erhalt auf Vollständigkeit.</p>`;
  await shareHtml(wrap(`Lieferschein ${order.id}`, inner), `Lieferschein-${order.id}.pdf`);
}

const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

export async function shareCollectivePdf(data: any) {
  const c = data.company;
  const inner = `
    <h1>Sammelrechnung ${MONTHS[data.month - 1]} ${data.year}</h1>
    <div class="box" style="margin-top:16px;">
      <strong>${c?.name ?? "Kunde"}</strong><br/>
      <span class="muted">${c?.city ?? ""} · USt-ID: ${c?.vatId ?? "-"}</span>
    </div>
    <p class="muted">Enthaltene Bestellungen: ${(data.orders || []).map((o: any) => o.id).join(", ") || "keine"}</p>
    <table>
      <tr><th>Position</th><th class="right">Menge</th><th class="right">Preis</th><th class="right">MwSt</th><th class="right">Netto</th></tr>
      ${vatRows(data.lineItems || [])}
    </table>
    <div class="right muted">Nettobetrag: ${euro(data.net)}</div>
    ${taxSummary(data.taxBreakdown)}
    <div class="right total" style="margin-top:6px;">Gesamt (brutto): ${euro(data.amount)}</div>`;
  await shareHtml(wrap(`Sammelrechnung-${data.year}-${data.month}`, inner), `Sammelrechnung-${c?.name ?? ""}-${data.year}-${data.month}.pdf`);
}
