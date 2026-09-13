import { useState } from "react";
import { View, Text, ScrollView, Pressable, Alert } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as WebBrowser from "expo-web-browser";
import { ArrowLeft, Phone, EnvelopeSimple, MapPin, Check, PencilSimple, Export, CreditCard } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet, apiPost, apiPut } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { shareInvoicePdf, shareCollectivePdf } from "@/src/lib/pdf";
import { Card, InfoRow, Button, Input, StatusBadge, EmptyState, Muted } from "@/src/components/ui";

export default function KundeDetail() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const qc = useQueryClient();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [tab, setTab] = useState<"konditionen" | "rechnungen" | "historie">("konditionen");
  const [editing, setEditing] = useState(false);
  const isAdmin = user?.role === "admin";

  const company = useQuery({ queryKey: ["company", id], queryFn: () => apiGet(`/companies/${id}`) });
  const prices = useQuery({ queryKey: ["prices", id], queryFn: () => apiGet(`/companies/${id}/prices`) });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => apiGet("/orders") });
  const invoices = useQuery({ queryKey: ["invoices"], queryFn: () => apiGet("/invoices") });
  const history = useQuery({
    queryKey: ["price-history", id],
    queryFn: () => apiGet(`/companies/${id}/price-history`),
  });

  const c = company.data;
  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));
  const custOrders = (orders.data ?? []).filter((o: any) => o.companyId === id);
  const custInvoices = (invoices.data ?? []).filter((i: any) => i.companyId === id);

  const payInvoice = useMutation({
    mutationFn: (invId: string) => apiPut(`/invoices/${invId}/pay`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const [payingId, setPayingId] = useState<string | null>(null);
  const payOnline = async (invId: string) => {
    setPayingId(invId);
    try {
      const res = await apiPost(`/invoices/${invId}/checkout`, {});
      if (res?.url) {
        await WebBrowser.openBrowserAsync(res.url);
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 1500));
          const st = await apiGet(`/invoices/${invId}/payment-status`);
          if (st.status === "Bezahlt") {
            qc.invalidateQueries({ queryKey: ["invoices"] });
            qc.invalidateQueries({ queryKey: ["dashboard"] });
            break;
          }
        }
      }
    } catch (e: any) {
      Alert.alert("Zahlung", e.message || "Online-Zahlung ist erst nach dem Deploy verfügbar.");
    } finally {
      setPayingId(null);
    }
  };

  const [sammelBusy, setSammelBusy] = useState(false);
  const sammelrechnung = async () => {
    setSammelBusy(true);
    try {
      const now = new Date();
      const data = await apiGet(`/companies/${id}/collective-invoice?year=${now.getFullYear()}&month=${now.getMonth() + 1}`);
      await shareCollectivePdf(data);
    } catch (e: any) {
      Alert.alert("Sammelrechnung", e.message || "Fehler");
    } finally {
      setSammelBusy(false);
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title} numberOfLines={1}>
            {c?.name ?? "Kunde"}
          </Text>
          {c ? <Text style={styles.subtitle}>{c.city}</Text> : null}
        </View>
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {c && (
          <Card testID="company-info-card">
            <View style={styles.metaRow}>
              <MapPin size={15} color={colors.muted} />
              <Text style={styles.metaText}>{c.city}</Text>
            </View>
            <View style={styles.metaRow}>
              <EnvelopeSimple size={15} color={colors.muted} />
              <Text style={styles.metaText}>{c.email}</Text>
            </View>
            <View style={styles.metaRow}>
              <Phone size={15} color={colors.muted} />
              <Text style={styles.metaText}>{c.phone}</Text>
            </View>
            <View style={styles.divider} />
            <InfoRow label="USt-ID" value={c.vatId} />
            <InfoRow label="Monatsabsatz" value={`${num(c.monthlyKg)} kg`} />
            <InfoRow label="Bestellzyklus" value={`${num(c.orderCycleDays ?? 30)} Tage`} />
            {isAdmin && (
              <Pressable testID="edit-company" style={styles.editBtn} onPress={() => setEditing(true)}>
                <PencilSimple size={16} color={colors.brandPrimary} weight="bold" />
                <Text style={styles.editText}>Firma bearbeiten</Text>
              </Pressable>
            )}
          </Card>
        )}

        {isAdmin && editing && c && (
          <CompanyEditor
            company={c}
            onCancel={() => setEditing(false)}
            onSaved={() => {
              setEditing(false);
              qc.invalidateQueries({ queryKey: ["company", id] });
              qc.invalidateQueries({ queryKey: ["companies"] });
            }}
          />
        )}

        <View style={styles.segment}>
          {(["konditionen", "rechnungen", "historie"] as const).map((t) => (
            <Pressable
              key={t}
              testID={`segment-${t}`}
              style={[styles.segItem, tab === t && styles.segItemActive]}
              onPress={() => setTab(t)}
            >
              <Text style={[styles.segText, tab === t && styles.segTextActive]}>
                {t === "konditionen" ? "Konditionen" : t === "rechnungen" ? "Rechnungen" : "Historie"}
              </Text>
            </Pressable>
          ))}
        </View>

        {tab === "konditionen" ? (
          <>
            {isAdmin ? (
              <AdminConditions
                companyId={id!}
                products={products.data ?? []}
                prices={prices.data ?? []}
              />
            ) : (prices.data ?? []).length === 0 ? (
              <Muted>Keine individuellen Konditionen hinterlegt.</Muted>
            ) : (
              (prices.data ?? []).map((cp: any) => {
                const p = prodMap[cp.productId];
                return (
                  <Card key={cp.productId} testID={`price-${cp.productId}`}>
                    <Text style={styles.prodTitle}>
                      {p ? `${p.brand} ${p.name}` : cp.productId}
                    </Text>
                    <InfoRow label="Kundenpreis" value={`${euro(cp.price)}/${p?.unit ?? "kg"}`} />
                    {p ? <InfoRow label="Standardpreis" value={euro(p.standardPrice)} /> : null}
                    {p?.salesFloor ? <InfoRow label="Vertriebslimit" value={euro(p.salesFloor)} /> : null}
                  </Card>
                );
              })
            )}
            <Text style={styles.histTitle}>Preisänderungen</Text>
            {(history.data ?? []).length === 0 ? (
              <Muted>Noch keine Preisänderungen erfasst.</Muted>
            ) : (
              (history.data ?? []).map((h: any, idx: number) => {
                const p = prodMap[h.productId];
                return (
                  <Card key={idx} testID={`history-${idx}`}>
                    <Text style={styles.prodTitle}>{p ? `${p.brand} ${p.name}` : h.productId}</Text>
                    <Muted>
                      {h.oldPrice != null ? `${euro(h.oldPrice)} → ` : "Neu: "}
                      {euro(h.newPrice)}
                    </Muted>
                    <Muted>
                      {dateDE(h.changedAt)} · {h.changedByName}
                    </Muted>
                  </Card>
                );
              })
            )}
          </>
        ) : tab === "rechnungen" ? (
          <>
            {(isAdmin || user?.role === "sales") && (
              <Pressable testID="sammelrechnung" style={styles.sammelBtn} onPress={sammelrechnung} disabled={sammelBusy}>
                <Export size={16} color={colors.brandPrimary} weight="bold" />
                <Text style={styles.editText}>{sammelBusy ? "Erstelle…" : "Sammelrechnung (Monat) als PDF"}</Text>
              </Pressable>
            )}
            {custInvoices.length === 0 ? (
              <EmptyState title="Keine Rechnungen" subtitle="Für diesen Kunden liegen keine Rechnungen vor" />
            ) : (
              custInvoices.map((inv: any) => (
                <Card key={inv.id} testID={`invoice-${inv.id}`}>
                  <View style={styles.orderTop}>
                    <Text style={styles.prodTitle}>{inv.id}</Text>
                    <StatusBadge status={inv.status} />
                  </View>
                  <InfoRow label="Datum" value={dateDE(inv.date)} />
                  {inv.net != null ? <InfoRow label="Netto" value={euro(inv.net)} /> : null}
                  {inv.taxTotal != null ? <InfoRow label="MwSt" value={euro(inv.taxTotal)} /> : null}
                  <InfoRow label={inv.net != null ? "Brutto" : "Betrag"} value={euro(inv.amount)} />
                  <Pressable testID={`invoice-pdf-${inv.id}`} style={styles.sammelBtn} onPress={() => shareInvoicePdf(inv, c)}>
                    <Export size={16} color={colors.brandPrimary} weight="bold" />
                    <Text style={styles.editText}>Rechnung als PDF</Text>
                  </Pressable>
                  {inv.status !== "Bezahlt" ? (
                    <>
                      <Button
                        testID={`pay-online-${inv.id}`}
                        title="Online bezahlen (Karte)"
                        loading={payingId === inv.id}
                        onPress={() => payOnline(inv.id)}
                        style={{ marginTop: 8 }}
                      />
                      <Button
                        testID={`pay-invoice-${inv.id}`}
                        title="Als bezahlt markieren"
                        kind="secondary"
                        loading={payInvoice.isPending && payInvoice.variables === inv.id}
                        onPress={() => payInvoice.mutate(inv.id)}
                        style={{ marginTop: 8 }}
                      />
                    </>
                  ) : null}
                </Card>
              ))
            )}
          </>
        ) : custOrders.length === 0 ? (
          <EmptyState title="Keine Bestellungen" subtitle="Für diesen Kunden liegen keine Bestellungen vor" />
        ) : (
          custOrders.map((o: any) => {
            const total = o.items.reduce((a: number, i: any) => a + i.price * i.qty, 0);
            return (
              <Pressable key={o.id} testID={`order-${o.id}`} onPress={() => router.push(`/bestellung/${o.id}`)}>
                <Card>
                  <View style={styles.orderTop}>
                    <Text style={styles.prodTitle}>{o.id}</Text>
                    <StatusBadge status={o.status} />
                  </View>
                  <InfoRow label="Datum" value={dateDE(o.createdAt)} />
                  <InfoRow label="Betrag" value={`${euro(total)} netto`} />
                </Card>
              </Pressable>
            );
          })
        )}
      </ScrollView>

      <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
        <Button
          testID="create-offer-cta"
          title="Neues Angebot erstellen"
          onPress={() => router.push({ pathname: "/(tabs)/angebote", params: { companyId: id } })}
        />
      </View>
    </View>
  );
}

function AdminConditions({
  companyId,
  products,
  prices,
}: {
  companyId: string;
  products: any[];
  prices: any[];
}) {
  const qc = useQueryClient();
  const priceMap: Record<string, number> = {};
  prices.forEach((cp: any) => (priceMap[cp.productId] = cp.price));

  return (
    <>
      <Muted>Kundenpreise festlegen. Leer = Standardpreis gilt.</Muted>
      {products.map((p: any) => (
        <PriceEditorRow
          key={p.id}
          product={p}
          companyId={companyId}
          initial={priceMap[p.id]}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["prices", companyId] });
            qc.invalidateQueries({ queryKey: ["price-history", companyId] });
          }}
        />
      ))}
    </>
  );
}

function PriceEditorRow({
  product,
  companyId,
  initial,
  onSaved,
}: {
  product: any;
  companyId: string;
  initial?: number;
  onSaved: () => void;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const [val, setVal] = useState(initial != null ? String(initial) : "");
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: () =>
      apiPost("/customer-prices", {
        companyId,
        productId: product.id,
        price: Number((val || "").replace(",", ".")),
      }),
    onSuccess: () => {
      setSaved(true);
      onSaved();
      setTimeout(() => setSaved(false), 1500);
    },
  });

  const parsed = Number((val || "").replace(",", "."));
  const belowFloor = parsed && parsed < product.absoluteFloor;

  return (
    <Card testID={`price-edit-${product.id}`}>
      <Text style={styles.prodTitle}>
        {product.brand} {product.name}
      </Text>
      <Muted>
        Standard {euro(product.standardPrice)} · Grenze {euro(product.absoluteFloor)}
      </Muted>
      <View style={styles.priceRow}>
        <Input
          testID={`price-input-${product.id}`}
          value={val}
          onChangeText={setVal}
          keyboardType="decimal-pad"
          placeholder={`${product.standardPrice}`}
          style={{ flex: 1 }}
        />
        <Pressable
          testID={`price-save-${product.id}`}
          disabled={!!belowFloor || save.isPending}
          onPress={() => save.mutate()}
          style={[styles.priceSave, (belowFloor || save.isPending) && { opacity: 0.5 }]}
        >
          <Check size={18} color={colors.onBrandPrimary} weight="bold" />
        </Pressable>
      </View>
      {saved ? <Text style={[styles.savedTxt, { color: colors.success }]}>Gespeichert</Text> : null}
      {belowFloor ? (
        <Text style={[styles.savedTxt, { color: colors.error }]}>Unter absoluter Preisgrenze</Text>
      ) : null}
    </Card>
  );
}


function CompanyEditor({ company, onCancel, onSaved }: { company: any; onCancel: () => void; onSaved: () => void }) {
  const styles = useStyles();
  const [form, setForm] = useState({
    name: company.name ?? "",
    city: company.city ?? "",
    email: company.email ?? "",
    phone: company.phone ?? "",
    vatId: company.vatId ?? "",
    orderCycleDays: String(company.orderCycleDays ?? 30),
  });
  const set = (k: string) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const save = useMutation({
    mutationFn: () =>
      apiPut(`/companies/${company.id}`, {
        name: form.name,
        city: form.city,
        email: form.email,
        phone: form.phone,
        vatId: form.vatId,
        assignedSalesRepId: company.assignedSalesRepId ?? null,
        orderCycleDays: Number((form.orderCycleDays || "30").replace(",", ".")) || 30,
        active: company.active !== false,
      }),
    onSuccess: onSaved,
  });
  return (
    <Card testID="company-editor">
      <Text style={styles.prodTitle}>Firma bearbeiten</Text>
      <Text style={styles.editLabel}>Name</Text>
      <Input testID="edit-name" value={form.name} onChangeText={set("name")} />
      <Text style={styles.editLabel}>Ort</Text>
      <Input testID="edit-city" value={form.city} onChangeText={set("city")} />
      <Text style={styles.editLabel}>E-Mail</Text>
      <Input testID="edit-email" value={form.email} onChangeText={set("email")} autoCapitalize="none" keyboardType="email-address" />
      <Text style={styles.editLabel}>Telefon</Text>
      <Input testID="edit-phone" value={form.phone} onChangeText={set("phone")} keyboardType="phone-pad" />
      <Text style={styles.editLabel}>USt-ID</Text>
      <Input testID="edit-vat" value={form.vatId} onChangeText={set("vatId")} autoCapitalize="characters" />
      <Text style={styles.editLabel}>Bestellzyklus (Tage)</Text>
      <Input testID="edit-cycle" value={form.orderCycleDays} onChangeText={set("orderCycleDays")} keyboardType="numeric" />
      <Button testID="edit-save" title="Speichern" loading={save.isPending} onPress={() => save.mutate()} style={{ marginTop: 10 }} />
      <Button testID="edit-cancel" title="Abbrechen" kind="secondary" onPress={onCancel} style={{ marginTop: 8 }} />
    </Card>
  );
}


const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: {
    flexDirection: "row",
    alignItems: "flex-end",
    paddingHorizontal: 20,
    paddingBottom: 14,
    backgroundColor: c.surface,
    borderBottomWidth: 1,
    borderBottomColor: c.divider,
    gap: 12,
  },
  backBtn: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: c.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { fontSize: 24, fontWeight: "800", color: c.onSurface, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 2 },
  content: { padding: 20, gap: 12, paddingBottom: 24 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  metaText: { fontSize: 14, color: c.onSurfaceSecondary },
  divider: { height: 1, backgroundColor: c.divider, marginVertical: 6 },
  segment: {
    flexDirection: "row",
    backgroundColor: c.surfaceTertiary,
    borderRadius: 12,
    padding: 4,
    gap: 4,
  },
  segItem: { flex: 1, paddingVertical: 10, borderRadius: 9, alignItems: "center" },
  segItemActive: { backgroundColor: c.surface, shadowColor: "#000", shadowOpacity: 0.06, shadowRadius: 4, elevation: 1 },
  segText: { fontSize: 14, fontWeight: "700", color: c.muted },
  segTextActive: { color: c.onSurface },
  prodTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  histTitle: { fontSize: 16, fontWeight: "800", color: c.onSurface, marginTop: 12, marginBottom: 2 },
  orderTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  priceRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 4 },
  priceSave: {
    width: 50,
    height: 50,
    borderRadius: 12,
    backgroundColor: c.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
  savedTxt: { fontSize: 13, fontWeight: "700", marginTop: 4 },
  editBtn: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10, paddingVertical: 8, justifyContent: "center", borderRadius: 10, backgroundColor: c.brandTertiary },
  editText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  editLabel: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  sammelBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 12, backgroundColor: c.brandTertiary, marginTop: 6 },
  footer: {
    padding: 20,
    paddingTop: 12,
    backgroundColor: c.surface,
    borderTopWidth: 1,
    borderTopColor: c.divider,
  },
}));
