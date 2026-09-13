import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { Platform } from "react-native";
import { euro, num, dateDE } from "@/src/lib/format";

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

export async function shareInvoicePdf(invoice: any, company: any) {
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
