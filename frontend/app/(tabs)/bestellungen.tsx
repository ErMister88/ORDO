import { useState } from "react";
import { View, Text, ScrollView, KeyboardAvoidingView, Platform, Pressable } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Image } from "expo-image";
import { Minus, Plus, ImageSquare, CaretDown, Trash } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet, apiPost, fileUrl } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { ScreenHeader } from "@/src/components/screen-header";
import { Card, Button, Input, StatusBadge, SectionTitle, EmptyState, Muted } from "@/src/components/ui";

export default function Bestellungen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user } = useAuth();
  const qc = useQueryClient();
  const params = useLocalSearchParams<{ companyId?: string; productId?: string; qty?: string; createInvoice?: string }>();
  const isStaff = user?.role === "admin" || user?.role === "sales";
  const [staffCompanyId, setStaffCompanyId] = useState(params.companyId ?? "");
  const companyId = isStaff ? staffCompanyId : user?.companyId;
  const router = useRouter();

  const orders = useQuery({ queryKey: ["orders"], queryFn: () => apiGet("/orders") });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const companies = useQuery({ queryKey: ["companies"], queryFn: () => apiGet("/companies"), enabled: isStaff });
  const addresses = useQuery({ queryKey: ["customer-addresses", companyId], queryFn: () => apiGet(`/companies/${companyId}/addresses`), enabled: Boolean(companyId) });

  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));
  const activeProducts = (products.data ?? []).filter((p: any) => p.active !== false);
  const [selId, setSelId] = useState<string>(params.productId ?? "");
  const [qty, setQty] = useState(() => {
    const parsed = Number(params.qty);
    return parsed > 0 ? parsed : 18;
  });
  const [showProd, setShowProd] = useState(false);
  const [cart, setCart] = useState<{ productId: string; qty: number }[]>([]);
  const [oSearch, setOSearch] = useState("");
  const [oStatus, setOStatus] = useState("Alle");
  const [showCompanies, setShowCompanies] = useState(false);
  const [paymentMethod, setPaymentMethod] = useState("bank_transfer");
  const [createInvoice, setCreateInvoice] = useState(params.createInvoice === "1");
  const [billingAddressId, setBillingAddressId] = useState("");
  const [deliveryAddressId, setDeliveryAddressId] = useState("");

  const selProduct = activeProducts.find((p: any) => p.id === selId) ?? activeProducts[0];

  const selectedQuote = useQuery({
    queryKey: ["b2b-quote", companyId, selProduct?.id, qty],
    queryFn: () => apiPost("/pricing/b2b/quote", { companyId, items: [{ productId: selProduct.id, qty }] }),
    enabled: !!companyId && !!selProduct,
  });
  const cartQuote = useQuery({
    queryKey: ["b2b-cart-quote", companyId, cart],
    queryFn: () => apiPost("/pricing/b2b/quote", { companyId, items: cart }),
    enabled: !!companyId && cart.length > 0,
  });
  const quoteMap: Record<string, any> = {};
  (cartQuote.data?.lines ?? []).forEach((line: any) => { quoteMap[line.productId] = line; });

  const addToCart = () => {
    if (!selProduct) return;
    setCart((c) => {
      const existing = c.find((i) => i.productId === selProduct.id);
      if (existing) return c.map((i) => (i.productId === selProduct.id ? { ...i, qty: i.qty + qty } : i));
      return [...c, { productId: selProduct.id, qty }];
    });
  };
  const removeItem = (pid: string) => setCart((c) => c.filter((i) => i.productId !== pid));
  const cartTotal = cartQuote.data?.total ?? 0;

  const create = useMutation({
    mutationFn: () =>
      apiPost("/orders", {
        companyId,
        items: cart.map((i) => ({ productId: i.productId, qty: i.qty })),
        paymentMethod,
        createInvoice,
        billingAddressId: billingAddressId || null,
        deliveryAddressId: deliveryAddressId || null,
      }),
    onSuccess: () => {
      setCart([]);
      qc.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  return (
    <View style={styles.root}>
      <ScreenHeader title="Bestellungen" subtitle="Nachbestellen & Historie" />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {isStaff ? <Card testID="staff-customer-selection"><SectionTitle>Direktverkauf</SectionTitle><Muted>Kunde auswählen, serverseitig gültige Preise verwenden und optional eine Rechnung erzeugen.</Muted><Pressable testID="staff-company-picker" style={styles.picker} onPress={() => setShowCompanies((value) => !value)}><Text style={styles.optionText}>{(companies.data ?? []).find((company: any) => company.id === companyId)?.name ?? "Kunde auswählen"}</Text><CaretDown size={18} color={colors.muted} /></Pressable>{showCompanies ? (companies.data ?? []).map((company: any) => <Pressable key={company.id} testID={`staff-company-${company.id}`} style={styles.option} onPress={() => { setStaffCompanyId(company.id); setShowCompanies(false); setCart([]); }}><Text style={styles.optionText}>{company.name}</Text></Pressable>) : null}</Card> : null}
          {companyId && (addresses.data ?? []).length > 0 ? <Card><SectionTitle>Dokumentadressen</SectionTitle><Muted>Rechnungs- und Lieferadresse werden als unveränderlicher Snapshot gespeichert.</Muted><Text style={styles.fieldLabel}>Rechnungsadresse</Text><View style={styles.addressChips}>{(addresses.data ?? []).filter((row: any) => row.type === "billing" || row.type === "main").map((row: any) => <Pressable key={row.id} onPress={() => setBillingAddressId(row.id)} style={[styles.addressChip, billingAddressId === row.id && styles.addressChipActive]}><Text style={[styles.addressChipText, billingAddressId === row.id && styles.addressChipTextActive]}>{row.label || `${row.street} ${row.houseNumber}`}</Text></Pressable>)}</View><Text style={styles.fieldLabel}>Lieferadresse</Text><View style={styles.addressChips}>{(addresses.data ?? []).filter((row: any) => row.type === "shipping" || row.type === "main").map((row: any) => <Pressable key={row.id} onPress={() => setDeliveryAddressId(row.id)} style={[styles.addressChip, deliveryAddressId === row.id && styles.addressChipActive]}><Text style={[styles.addressChipText, deliveryAddressId === row.id && styles.addressChipTextActive]}>{row.label || `${row.street} ${row.houseNumber}`}</Text></Pressable>)}</View></Card> : null}
          {companyId && selProduct && (
            <Card testID="reorder-card">
              <SectionTitle>Nachbestellen</SectionTitle>

              <Pressable testID="reorder-product" style={styles.picker} onPress={() => setShowProd((v) => !v)}>
                <View style={styles.reorderHead}>
                  {selProduct.imageUrl ? (
                    <Image source={{ uri: fileUrl(selProduct.imageUrl) }} style={styles.reorderThumb} contentFit="cover" transition={150} />
                  ) : (
                    <View style={[styles.reorderThumb, styles.reorderThumbEmpty]}>
                      <ImageSquare size={24} color={colors.muted} weight="duotone" />
                    </View>
                  )}
                  <View style={{ flex: 1 }}>
                    <Text style={styles.prodTitle}>
                      {selProduct.brand} {selProduct.name}
                    </Text>
                    <Muted>Ihr Preis: {selectedQuote.data ? euro(selectedQuote.data.lines[0].finalUnitPrice) : "wird berechnet"}/{selProduct.unit} netto</Muted>
                  </View>
                  <CaretDown size={18} color={colors.muted} weight="bold" />
                </View>
              </Pressable>
              {showProd &&
                activeProducts.map((p: any) => (
                  <Pressable
                    key={p.id}
                    testID={`reorder-opt-${p.id}`}
                    style={styles.option}
                    onPress={() => {
                      setSelId(p.id);
                      setShowProd(false);
                    }}
                  >
                    <Text style={styles.optionText}>
                      {p.brand} {p.name}
                    </Text>
                  </Pressable>
                ))}

              <Muted>Alle Preise netto zzgl. gesetzlicher MwSt.</Muted>
              {selProduct.stock != null && qty > selProduct.stock ? (
                <Text testID="stock-warning" style={styles.stockWarn}>
                  Hinweis: nur noch {num(selProduct.stock)} {selProduct.unit} auf Lager – Bestellung dennoch möglich.
                </Text>
              ) : null}

              <View style={styles.stepper}>
                <Pressable testID="qty-minus" style={styles.stepBtn} onPress={() => setQty((v) => Math.max(1, v - 1))}>
                  <Minus size={18} color={colors.onSurface} weight="bold" />
                </Pressable>
                <View style={styles.qtyBox}>
                  <Text style={styles.qtyValue}>{qty}</Text>
                  <Text style={styles.qtyUnit}>{selProduct.unit}</Text>
                </View>
                <Pressable testID="qty-plus" style={styles.stepBtn} onPress={() => setQty((v) => v + 1)}>
                  <Plus size={18} color={colors.onSurface} weight="bold" />
                </Pressable>
              </View>

              <Button testID="add-to-cart" title="Zur Bestellung hinzufügen" kind="secondary" onPress={addToCart} style={{ marginTop: 4 }} />

              {cart.length > 0 && (
                <View style={styles.cartBox} testID="cart">
                  {cart.map((i) => {
                    const p = prodMap[i.productId];
                    const up = quoteMap[i.productId]?.finalUnitPrice ?? 0;
                    return (
                      <View key={i.productId} style={styles.cartRow} testID={`cart-row-${i.productId}`}>
                        <Text style={styles.cartName} numberOfLines={1}>
                          {p ? `${p.brand} ${p.name}` : i.productId}
                        </Text>
                        <Text style={styles.cartQty}>
                          {num(i.qty)} × {euro(up)}
                        </Text>
                        <Text style={styles.cartLine}>{euro(up * i.qty)}</Text>
                        <Pressable testID={`cart-remove-${i.productId}`} onPress={() => removeItem(i.productId)} hitSlop={8}>
                          <Trash size={16} color={colors.error} weight="bold" />
                        </Pressable>
                      </View>
                    );
                  })}
                  <View style={styles.totalRow}>
                    <Text style={styles.totalLabel}>Netto gesamt</Text>
                    <Text style={styles.totalValue}>{euro(cartTotal)}</Text>
                  </View>
                  {cartQuote.error ? <Text style={{ color: colors.error }}>{(cartQuote.error as Error).message}</Text> : null}
                  {isStaff ? <><Text style={styles.paymentTitle}>Zahlungsart</Text><View style={styles.paymentRow}>{[["bank_transfer", "Überweisung"], ["cash", "Bar"], ["card", "Karte"], ["other", "Sonstige"]].map(([value, label]) => <Pressable key={value} testID={`payment-${value}`} onPress={() => setPaymentMethod(value)} style={[styles.filterChip, paymentMethod === value && styles.filterChipActive]}><Text style={[styles.filterChipText, paymentMethod === value && styles.filterChipTextActive]}>{label}</Text></Pressable>)}</View><Pressable testID="create-invoice-toggle" onPress={() => setCreateInvoice((value) => !value)} style={[styles.invoiceToggle, createInvoice && styles.invoiceToggleActive]}><Text style={[styles.filterChipText, createInvoice && styles.filterChipTextActive]}>{createInvoice ? "✓ Rechnung wird erstellt" : "+ Rechnung optional erstellen"}</Text></Pressable></> : null}
                  <Button testID="submit-order" title="Bestellung aufgeben" loading={create.isPending || cartQuote.isLoading} disabled={!cartQuote.data} onPress={() => create.mutate()} />
                </View>
              )}
            </Card>
          )}

          <SectionTitle style={{ marginTop: 4 }}>Bestellhistorie</SectionTitle>
          <Input
            testID="order-search"
            value={oSearch}
            onChangeText={setOSearch}
            placeholder="Suche (Bestellnr.)…"
            style={{ marginBottom: 8 }}
          />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
            {["Alle", "Neu", "Bestätigt", "Kommissioniert", "Versendet", "Abgeschlossen", "Storniert"].map((s) => (
              <Pressable
                key={s}
                testID={`order-filter-${s}`}
                style={[styles.filterChip, oStatus === s && styles.filterChipActive]}
                onPress={() => setOStatus(s)}
              >
                <Text style={[styles.filterChipText, oStatus === s && styles.filterChipTextActive]}>{s}</Text>
              </Pressable>
            ))}
          </ScrollView>
          {(() => {
            const q = oSearch.trim().toLowerCase();
            const list = (orders.data ?? []).filter((o: any) => {
              if (oStatus !== "Alle" && o.status !== oStatus) return false;
              return !q || o.id.toLowerCase().includes(q);
            });
            return list.length === 0 ? (
              <EmptyState title="Keine Bestellungen" subtitle="Keine Treffer für diese Filter" />
            ) : (
              list.map((o: any) => {
                const total = o.netTotalMinor != null
                  ? o.netTotalMinor / 100
                  : o.items.reduce((a: number, i: any) => a + i.price * i.qty, 0);
                return (
                  <Pressable key={o.id} testID={`order-${o.id}`} onPress={() => router.push(`/bestellung/${o.id}`)}>
                    <Card>
                      <View style={styles.orderTop}>
                        <Text style={styles.prodTitle}>{o.id}</Text>
                        <StatusBadge status={o.status} />
                      </View>
                      <Muted>
                        {dateDE(o.createdAt)} · {o.items.length} Position(en) · {euro(total)} netto
                      </Muted>
                    </Card>
                  </Pressable>
                );
              })
            );
          })()}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  prodTitle: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  stepper: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 6 },
  stepBtn: {
    width: 48,
    height: 48,
    borderRadius: 12,
    backgroundColor: c.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  qtyBox: {
    flex: 1,
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "center",
    gap: 4,
    backgroundColor: c.surfaceTertiary,
    borderRadius: 12,
    paddingVertical: 12,
  },
  qtyValue: { fontSize: 24, fontWeight: "800", color: c.onSurface },
  qtyUnit: { fontSize: 14, color: c.muted, fontWeight: "600" },
  totalRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 6,
  },
  totalLabel: { fontSize: 15, color: c.muted, fontWeight: "600" },
  totalValue: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  orderTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  tierActive: { fontSize: 13, fontWeight: "800", color: c.success, marginTop: 2 },
  reorderHead: { flexDirection: "row", gap: 12, alignItems: "center", marginTop: 4 },
  reorderThumb: { width: 56, height: 56, borderRadius: 12, backgroundColor: c.surfaceTertiary },
  reorderThumbEmpty: { alignItems: "center", justifyContent: "center" },
  picker: { borderRadius: 12, backgroundColor: c.surfaceTertiary, padding: 10 },
  option: { paddingVertical: 12, paddingHorizontal: 12, borderRadius: 10, backgroundColor: c.surfaceTertiary, marginTop: 6 },
  optionText: { fontSize: 15, fontWeight: "600", color: c.onSurface },
  stockWarn: { fontSize: 13, fontWeight: "700", color: c.warning ?? c.error, marginTop: 6 },
  cartBox: { marginTop: 12, gap: 8, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 12 },
  cartRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  cartName: { flex: 1, fontSize: 14, fontWeight: "700", color: c.onSurface },
  cartQty: { fontSize: 13, color: c.muted, fontWeight: "600" },
  cartLine: { fontSize: 14, fontWeight: "800", color: c.onSurface, minWidth: 64, textAlign: "right" },
  filterRow: { gap: 8, paddingBottom: 10 },
  filterChip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, backgroundColor: c.surfaceTertiary },
  filterChipActive: { backgroundColor: c.brandPrimary },
  filterChipText: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary },
  filterChipTextActive: { color: c.onBrandPrimary },
  paymentTitle: { color: c.onSurface, fontSize: 13, fontWeight: "800", marginTop: 6 },
  fieldLabel: { color: c.onSurfaceSecondary, fontSize: 13, fontWeight: "800", marginTop: 8 },
  addressChips: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 5 },
  addressChip: { borderWidth: 1, borderColor: c.border, borderRadius: 999, paddingHorizontal: 11, paddingVertical: 7 },
  addressChipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  addressChipText: { color: c.onSurfaceSecondary, fontSize: 12, fontWeight: "700" },
  addressChipTextActive: { color: c.onBrandPrimary },
  paymentRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  invoiceToggle: { borderWidth: 1, borderColor: c.border, borderRadius: 10, padding: 11, alignItems: "center" },
  invoiceToggleActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
}));
