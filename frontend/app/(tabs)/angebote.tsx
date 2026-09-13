import { useMemo, useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams } from "expo-router";
import { CaretDown } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet, apiPost } from "@/src/api/client";
import { euro, num } from "@/src/lib/format";
import { ScreenHeader } from "@/src/components/screen-header";
import { Card, Input, Button, StatusBadge, SectionTitle, EmptyState, Muted } from "@/src/components/ui";

export default function Angebote() {
  const styles = useStyles();
  const { user } = useAuth();
  const qc = useQueryClient();
  const params = useLocalSearchParams<{ companyId?: string }>();
  const isStaff = user?.role === "admin" || user?.role === "sales";
  const isAdmin = user?.role === "admin";

  const offers = useQuery({ queryKey: ["offers"], queryFn: () => apiGet("/offers") });
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

          {(offers.data ?? []).length === 0 ? (
            <EmptyState title="Keine offenen Angebote" subtitle="Erstellte Angebote erscheinen hier" />
          ) : (
            (offers.data ?? []).map((o: any) => {
              const i = o.items[0];
              const p = prodMap[i.productId];
              const comp = compMap[o.companyId];
              const db = p ? (i.price - p.cost) * i.qty : null;
              return (
                <Card key={o.id} testID={`offer-${o.id}`}>
                  <View style={styles.offerTop}>
                    <Text style={styles.offerId}>{o.id}</Text>
                    <StatusBadge status={o.status} />
                  </View>
                  {comp ? <Text style={styles.offerCompany}>{comp.name}</Text> : null}
                  <Muted>
                    {p ? `${p.brand} ${p.name}` : i.productId} · {num(i.qty)} kg · {euro(i.price)}/kg
                  </Muted>
                  {o.reason ? <Muted style={{ fontStyle: "italic" }}>{`„${o.reason}"`}</Muted> : null}

                  {isAdmin && o.status === "Freigabe nötig" && (
                    <View style={styles.actions}>
                      {db != null ? (
                        <Muted>DB: {euro(db)}/Monat · Vertrag ca. {euro(db * o.termMonths)}</Muted>
                      ) : null}
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
          )}
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
  const [companyId, setCompanyId] = useState(defaultCompanyId || companies[0]?.id || "");
  const [productId, setProductId] = useState(products[0]?.id || "");
  const [qty, setQty] = useState("60");
  const [price, setPrice] = useState("");
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

  const product = products.find((p) => p.id === productId) || products[0];
  const parsed = Number((price || "").replace(",", "."));
  const q = Number(qty) || 0;

  const state = useMemo(() => {
    if (!product || !parsed) return null;
    if (parsed < product.absoluteFloor) return "invalid";
    if (parsed < product.salesFloor) return "approval";
    return "ok";
  }, [product, parsed]);

  const db = product && parsed ? (parsed - product.cost) * q : 0;

  const create = useMutation({
    mutationFn: () =>
      apiPost("/offers", {
        companyId,
        termMonths: 48,
        items: [{ productId, qty: q, price: parsed }],
      }),
    onSuccess: () => {
      setMsg("Angebot erstellt");
      setPrice("");
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

      <Text style={styles.fieldLabel}>Produkt</Text>
      <Pressable style={styles.select} testID="select-product" onPress={() => setShowProd((s) => !s)}>
        <Text style={styles.selectText}>
          {product.brand} {product.name}
        </Text>
        <CaretDown size={16} color={colors.muted} />
      </Pressable>
      {showProd &&
        products.map((p) => (
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
      {parsed ? (
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

      {msg ? <Text style={[styles.hint, { color: colors.brandPrimary }]}>{msg}</Text> : null}

      <Button
        testID="create-offer-submit"
        title="Angebot erstellen"
        disabled={!parsed || !q || state === "invalid" || !companyId}
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
  actions: { gap: 10, marginTop: 6, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 10 },
  actionRow: { flexDirection: "row", gap: 10 },
}));
