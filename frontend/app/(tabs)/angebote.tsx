import { useMemo, useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Image } from "expo-image";
import { CaretDown, Export, Plus, ImageSquare } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet, apiPost, fileUrl } from "@/src/api/client";
import { euro, num } from "@/src/lib/format";
import { applicableTier } from "@/src/lib/pricing";
import { shareOfferPdf } from "@/src/lib/pdf";
import { ScreenHeader } from "@/src/components/screen-header";
import { Card, Input, Button, StatusBadge, SectionTitle, EmptyState, Muted } from "@/src/components/ui";

export default function Angebote() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user } = useAuth();
  const qc = useQueryClient();
  const router = useRouter();
  const params = useLocalSearchParams<{ companyId?: string }>();
  const isStaff = user?.role === "admin" || user?.role === "sales";
  const isAdmin = user?.role === "admin";

  const offers = useQuery({ queryKey: ["offers"], queryFn: () => apiGet("/offers") });
  const [oSearch, setOSearch] = useState("");
  const [oStatus, setOStatus] = useState<string>("Alle");
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: () => apiGet("/companies"),
    enabled: isStaff,
  });

  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));
  const compMap: Record<string, any> = {};
  (companies.data ?? []).forEach((c: any) => (compMap[c.id] = c));

  const decide = useMutation({
    mutationFn: ({ id, action, note }: { id: string; action: string; note: string }) =>
      apiPost(`/offers/${id}/${action}`, { note }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["offers"] }),
  });

  const accept = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) => apiPost(`/offers/${id}/accept`, { note }),
    onSuccess: (order: any) => {
      qc.invalidateQueries({ queryKey: ["offers"] });
      qc.invalidateQueries({ queryKey: ["orders"] });
      router.push(`/bestellung/${order.id}`);
    },
  });
  const [notes, setNotes] = useState<Record<string, string>>({});

  return (
    <View style={styles.root}>
      <ScreenHeader title="Angebote" subtitle={isStaff ? "Erstellen & freigeben" : "Ihre Angebote"} />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
        >
          {isStaff && (
            <CreateOffer
              products={products.data ?? []}
              companies={companies.data ?? []}
              defaultCompanyId={params.companyId}
              onCreated={() => qc.invalidateQueries({ queryKey: ["offers"] })}
            />
          )}

          <SectionTitle style={{ marginTop: 4 }}>
            {isAdmin ? "Alle Angebote" : "Angebote"}
          </SectionTitle>

          <Input
            testID="offer-search"
            value={oSearch}
            onChangeText={setOSearch}
            placeholder="Suche (Kunde, Angebotsnr.)…"
            style={{ marginBottom: 8 }}
          />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
            {["Alle", "Freigabe nötig", "Freigegeben", "Angenommen", "Abgelehnt"].map((s) => (
              <Pressable
                key={s}
                testID={`offer-filter-${s}`}
                style={[styles.filterChip, oStatus === s && styles.filterChipActive]}
                onPress={() => setOStatus(s)}
              >
                <Text style={[styles.filterChipText, oStatus === s && styles.filterChipTextActive]}>{s}</Text>
              </Pressable>
            ))}
          </ScrollView>

          {(() => {
            const q = oSearch.trim().toLowerCase();
            const visibleOffers = (offers.data ?? []).filter((o: any) => {
              if (oStatus !== "Alle" && o.status !== oStatus) return false;
              if (!q) return true;
              const cname = (compMap[o.companyId]?.name ?? "").toLowerCase();
              return o.id.toLowerCase().includes(q) || cname.includes(q);
            });
            return visibleOffers.length === 0 ? (
              <EmptyState title="Keine Angebote" subtitle="Keine Treffer für diese Filter" />
            ) : (
              visibleOffers.map((o: any) => {
              const comp = compMap[o.companyId];
              const monthlyDb = o.items.reduce((s: number, it: any) => {
                const p = prodMap[it.productId];
                return s + (p ? (it.price - p.cost) * it.qty : 0);
              }, 0);
              return (
                <Card key={o.id} testID={`offer-${o.id}`}>
                  <View style={styles.offerTop}>
                    <Text style={styles.offerId}>{o.id}</Text>
                    <StatusBadge status={o.status} />
                  </View>
                  {comp ? <Text style={styles.offerCompany}>{comp.name}</Text> : null}
                  {o.items.map((it: any, idx: number) => {
                    const p = prodMap[it.productId];
                    return (
                      <View key={idx} style={styles.lineItemRow}>
                        {p?.imageUrl ? (
                          <Image source={{ uri: fileUrl(p.imageUrl) }} style={styles.lineThumb} contentFit="cover" transition={150} />
                        ) : (
                          <View style={[styles.lineThumb, styles.lineThumbEmpty]}>
                            <ImageSquare size={16} color={colors.muted} weight="duotone" />
                          </View>
                        )}
                        <Muted style={{ flex: 1 }}>
                          {p ? `${p.brand} ${p.name}` : it.productId} · {num(it.qty)} kg · {euro(it.price)}/kg
                        </Muted>
                      </View>
                    );
                  })}
                  {o.reason ? <Muted style={{ fontStyle: "italic" }}>{`„${o.reason}"`}</Muted> : null}

                  {o.status === "Freigegeben" && (
                    <Pressable
                      testID={`share-offer-${o.id}`}
                      style={styles.shareBtn}
                      onPress={() => shareOfferPdf(o, comp, prodMap)}
                    >
                      <Export size={16} color={colors.brandPrimary} weight="bold" />
                      <Text style={styles.shareText}>Als PDF teilen</Text>
                    </Pressable>
                  )}

                  {!isStaff && o.status === "Freigegeben" && (
                    <>
                      <Input
                        testID={`accept-note-${o.id}`}
                        value={notes[o.id] ?? ""}
                        onChangeText={(v) => setNotes((n) => ({ ...n, [o.id]: v }))}
                        placeholder="Notiz zur Bestellung (optional)…"
                        style={{ marginTop: 8 }}
                      />
                      <Button
                        testID={`accept-offer-${o.id}`}
                        title="Angebot annehmen & bestellen"
                        kind="success"
                        loading={accept.isPending && accept.variables?.id === o.id}
                        onPress={() => accept.mutate({ id: o.id, note: notes[o.id] ?? "" })}
                        style={{ marginTop: 8 }}
                      />
                    </>
                  )}
                  {!isStaff && o.status === "Angenommen" && o.orderId && (
                    <Pressable
                      testID={`view-order-${o.id}`}
                      style={styles.shareBtn}
                      onPress={() => router.push(`/bestellung/${o.orderId}`)}
                    >
                      <Text style={styles.shareText}>Zur Bestellung {o.orderId}</Text>
                    </Pressable>
                  )}

                  {isAdmin && o.status === "Freigabe nötig" && (
                    <View style={styles.actions}>
                      <Muted>DB: {euro(monthlyDb)}/Monat · Vertrag ca. {euro(monthlyDb * o.termMonths)}</Muted>
                      <View style={styles.actionRow}>
                        <Button
                          testID={`approve-${o.id}`}
                          title="Freigeben"
                          kind="success"
                          style={{ flex: 1 }}
                          loading={decide.isPending}
                          onPress={() => decide.mutate({ id: o.id, action: "approve", note: "" })}
                        />
                        <Button
                          testID={`reject-${o.id}`}
                          title="Ablehnen"
                          kind="danger"
                          style={{ flex: 1 }}
                          loading={decide.isPending}
                          onPress={() => decide.mutate({ id: o.id, action: "reject", note: "" })}
                        />
                      </View>
                    </View>
                  )}
                </Card>
              );
            })
            );
          })()}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function CreateOffer({
  products,
  companies,
  defaultCompanyId,
  onCreated,
}: {
  products: any[];
  companies: any[];
  defaultCompanyId?: string;
  onCreated: () => void;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const activeProducts = products.filter((p) => p.active !== false);
  const [companyId, setCompanyId] = useState(defaultCompanyId || companies[0]?.id || "");
  const [productId, setProductId] = useState(activeProducts[0]?.id || "");
  const [qty, setQty] = useState("60");
  const [price, setPrice] = useState("");
  const [items, setItems] = useState<{ productId: string; qty: number; price: number }[]>([]);
  const [showComp, setShowComp] = useState(false);
  const [showProd, setShowProd] = useState(false);
  const [msg, setMsg] = useState("");

  const onQty = (v: string) => {
    setQty(v);
    if (msg) setMsg("");
  };
  const onPrice = (v: string) => {
    setPrice(v);
    if (msg) setMsg("");
  };

  const product = products.find((p) => p.id === productId) || activeProducts[0];
  const parsed = Number((price || "").replace(",", "."));
  const q = Number(qty) || 0;

  const state = useMemo(() => {
    if (!product || !parsed) return null;
    if (parsed < product.absoluteFloor) return "invalid";
    if (parsed < product.salesFloor) return "approval";
    return "ok";
  }, [product, parsed]);

  const db = product && parsed ? (parsed - product.cost) * q : 0;
  const tier = applicableTier(product?.discountTiers, q);

  const addItem = () => {
    if (!product || !parsed || !q) return;
    if (parsed < product.absoluteFloor) {
      setMsg("Position unter absoluter Preisgrenze – nicht zulässig.");
      return;
    }
    setItems((prev) => [...prev, { productId, qty: q, price: parsed }]);
    setPrice("");
    setQty("60");
    setMsg("");
  };

  const removeItem = (idx: number) => setItems((prev) => prev.filter((_, i) => i !== idx));

  const needsApproval = items.some((it) => {
    const p = products.find((x) => x.id === it.productId);
    return p && it.price < p.salesFloor;
  });

  const create = useMutation({
    mutationFn: () =>
      apiPost("/offers", {
        companyId,
        termMonths: 48,
        items,
      }),
    onSuccess: () => {
      setMsg("Angebot erstellt");
      setItems([]);
      onCreated();
    },
    onError: (e: any) => setMsg(e.message),
  });

  const company = companies.find((c) => c.id === companyId);

  if (!product) return null;

  return (
    <Card testID="create-offer-card">
      <SectionTitle>Neues Angebot</SectionTitle>

      <Text style={styles.fieldLabel}>Kunde</Text>
      <Pressable style={styles.select} testID="select-company" onPress={() => setShowComp((s) => !s)}>
        <Text style={styles.selectText}>{company?.name ?? "Kunde wählen"}</Text>
        <CaretDown size={16} color={colors.muted} />
      </Pressable>
      {showComp &&
        companies.map((c) => (
          <Pressable
            key={c.id}
            testID={`company-opt-${c.id}`}
            style={styles.option}
            onPress={() => {
              setCompanyId(c.id);
              setShowComp(false);
            }}
          >
            <Text style={styles.optionText}>{c.name}</Text>
          </Pressable>
        ))}

      {items.length > 0 && (
        <View style={styles.lineList}>
          {items.map((it, idx) => {
            const p = products.find((x) => x.id === it.productId);
            const below = p && it.price < p.salesFloor;
            return (
              <View key={idx} style={styles.lineRow} testID={`offer-line-${idx}`}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.lineTitle}>
                    {p ? `${p.brand} ${p.name}` : it.productId}
                  </Text>
                  <Text style={styles.lineSub}>
                    {num(it.qty)} kg · {euro(it.price)}/kg{below ? " · Freigabe nötig" : ""}
                  </Text>
                </View>
                <Pressable testID={`remove-line-${idx}`} onPress={() => removeItem(idx)} hitSlop={10}>
                  <Text style={[styles.removeTxt, { color: colors.error }]}>Entfernen</Text>
                </Pressable>
              </View>
            );
          })}
        </View>
      )}

      <Text style={styles.fieldLabel}>Produkt</Text>
      <Pressable style={styles.select} testID="select-product" onPress={() => setShowProd((s) => !s)}>
        <Text style={styles.selectText}>
          {product.brand} {product.name}
        </Text>
        <CaretDown size={16} color={colors.muted} />
      </Pressable>
      {showProd &&
        activeProducts.map((p) => (
          <Pressable
            key={p.id}
            testID={`product-opt-${p.id}`}
            style={styles.option}
            onPress={() => {
              setProductId(p.id);
              setShowProd(false);
            }}
          >
            <Text style={styles.optionText}>
              {p.brand} {p.name}
            </Text>
          </Pressable>
        ))}

      <View style={styles.inputRow}>
        <View style={{ flex: 1 }}>
          <Text style={styles.fieldLabel}>Menge (kg/Monat)</Text>
          <Input testID="offer-qty-input" value={qty} onChangeText={onQty} keyboardType="numeric" />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.fieldLabel}>Preis (€/kg)</Text>
          <Input
            testID="offer-price-input"
            value={price}
            onChangeText={onPrice}
            keyboardType="decimal-pad"
            placeholder={String(product.standardPrice)}
          />
        </View>
      </View>

      <Muted>
        Standard {euro(product.standardPrice)} · Vertriebslimit {euro(product.salesFloor)} · Grenze{" "}
        {euro(product.absoluteFloor)}
      </Muted>

      {product.discountTiers?.length ? (
        <View style={styles.tierBox} testID="tier-box">
          <Text style={styles.tierTitle}>Mengenrabatt-Staffeln</Text>
          {product.discountTiers.map((t: any, i: number) => (
            <Text
              key={i}
              style={[styles.tierRow, tier?.minQty === t.minQty && styles.tierRowActive]}
            >
              ab {num(t.minQty)} {product.unit} · {euro(t.price)}/{product.unit}
              {tier?.minQty === t.minQty ? "  ✓" : ""}
            </Text>
          ))}
          {tier ? (
            <Pressable testID="apply-tier" style={styles.applyTierBtn} onPress={() => onPrice(String(tier.price))}>
              <Text style={styles.applyTierTxt}>Staffelpreis {euro(tier.price)} übernehmen</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      {parsed && isAdmin ? (
        <Muted>
          DB/Monat: {euro(db)} · DB/kg: {euro(parsed - product.cost)}
        </Muted>
      ) : null}

      {state === "invalid" && (
        <Text style={[styles.hint, { color: colors.error }]}>
          Unter absoluter Preisgrenze – nicht zulässig.
        </Text>
      )}
      {state === "approval" && (
        <Text style={[styles.hint, { color: colors.warning }]}>Admin-Freigabe erforderlich.</Text>
      )}
      {state === "ok" && (
        <Text style={[styles.hint, { color: colors.success }]}>Direkt freigabefähig.</Text>
      )}

      <Pressable
        testID="add-line-button"
        style={[styles.addLineBtn, (!parsed || !q || state === "invalid") && { opacity: 0.5 }]}
        disabled={!parsed || !q || state === "invalid"}
        onPress={addItem}
      >
        <Plus size={16} color={colors.brandPrimary} weight="bold" />
        <Text style={styles.addLineTxt}>Position hinzufügen</Text>
      </Pressable>

      {items.length > 0 && needsApproval ? (
        <Muted style={{ marginTop: 4 }}>Enthält Positionen unter Vertriebslimit → Admin-Freigabe.</Muted>
      ) : null}

      {msg ? <Text style={[styles.hint, { color: colors.brandPrimary }]}>{msg}</Text> : null}

      <Button
        testID="create-offer-submit"
        title={`Angebot erstellen${items.length ? ` (${items.length})` : ""}`}
        disabled={items.length === 0 || !companyId}
        loading={create.isPending}
        onPress={() => {
          setMsg("");
          create.mutate();
        }}
        style={{ marginTop: 4 }}
      />
    </Card>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  fieldLabel: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  select: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: c.surfaceTertiary,
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  selectText: { fontSize: 15, fontWeight: "600", color: c.onSurface },
  option: {
    backgroundColor: c.surfaceTertiary,
    borderRadius: 10,
    paddingHorizontal: 16,
    paddingVertical: 12,
    marginTop: 4,
  },
  optionText: { fontSize: 15, color: c.onSurfaceSecondary },
  inputRow: { flexDirection: "row", gap: 12 },
  hint: { fontSize: 14, fontWeight: "700", marginTop: 4 },
  offerTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  offerId: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  offerCompany: { fontSize: 14, fontWeight: "700", color: c.brandPrimary },
  lineItemRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 4 },
  lineThumb: { width: 36, height: 36, borderRadius: 8, backgroundColor: c.surfaceTertiary },
  lineThumbEmpty: { alignItems: "center", justifyContent: "center" },
  filterRow: { gap: 8, paddingBottom: 10 },
  filterChip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, backgroundColor: c.surfaceTertiary },
  filterChipActive: { backgroundColor: c.brandPrimary },
  filterChipText: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary },
  filterChipTextActive: { color: c.onBrandPrimary },
  actions: { gap: 10, marginTop: 6, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 10 },
  actionRow: { flexDirection: "row", gap: 10 },
  shareBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: 4,
    paddingVertical: 10,
    borderRadius: 12,
    backgroundColor: c.brandTertiary,
  },
  shareText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  lineList: { gap: 8, marginTop: 8 },
  lineRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: c.surfaceTertiary,
    borderRadius: 12,
    padding: 12,
    gap: 10,
  },
  lineTitle: { fontSize: 14, fontWeight: "700", color: c.onSurface },
  lineSub: { fontSize: 13, color: c.muted, marginTop: 1 },
  removeTxt: { fontSize: 13, fontWeight: "700" },
  addLineBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: 8,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: c.brandPrimary,
    borderStyle: "dashed",
  },
  addLineTxt: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  tierBox: {
    marginTop: 8,
    padding: 12,
    borderRadius: 12,
    backgroundColor: c.surfaceTertiary,
    gap: 4,
  },
  tierTitle: { fontSize: 13, fontWeight: "800", color: c.onSurfaceSecondary, marginBottom: 2 },
  tierRow: { fontSize: 13, color: c.muted, fontWeight: "600" },
  tierRowActive: { color: c.success, fontWeight: "800" },
  applyTierBtn: {
    marginTop: 6,
    paddingVertical: 8,
    borderRadius: 10,
    backgroundColor: c.brandTertiary,
    alignItems: "center",
  },
  applyTierTxt: { color: c.brandPrimary, fontWeight: "700", fontSize: 13 },
}));
