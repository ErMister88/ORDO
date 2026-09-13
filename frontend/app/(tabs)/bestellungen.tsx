import { useState } from "react";
import { View, Text, ScrollView, KeyboardAvoidingView, Platform, Pressable } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { Minus, Plus } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet, apiPost } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { applicableTier } from "@/src/lib/pricing";
import { ScreenHeader } from "@/src/components/screen-header";
import { Card, Button, StatusBadge, SectionTitle, EmptyState, Muted } from "@/src/components/ui";

export default function Bestellungen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user } = useAuth();
  const qc = useQueryClient();
  const companyId = user?.companyId;
  const router = useRouter();

  const orders = useQuery({ queryKey: ["orders"], queryFn: () => apiGet("/orders") });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const prices = useQuery({
    queryKey: ["prices", companyId],
    queryFn: () => apiGet(`/companies/${companyId}/prices`),
    enabled: !!companyId,
  });

  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));
  const priceOf = (pid: string) =>
    (prices.data ?? []).find((cp: any) => cp.productId === pid)?.price ?? prodMap[pid]?.standardPrice ?? 0;

  const mainProduct = (products.data ?? [])[0];
  const [qty, setQty] = useState(18);

  const basePrice = mainProduct ? priceOf(mainProduct.id) : 0;
  const tier = applicableTier(mainProduct?.discountTiers, qty);
  const unitPrice = tier && tier.price < basePrice ? tier.price : basePrice;

  const create = useMutation({
    mutationFn: () =>
      apiPost("/orders", {
        companyId,
        items: [{ productId: mainProduct.id, qty, price: unitPrice }],
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  });

  return (
    <View style={styles.root}>
      <ScreenHeader title="Bestellungen" subtitle="Nachbestellen & Historie" />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {mainProduct && (
            <Card testID="reorder-card">
              <SectionTitle>Nachbestellen</SectionTitle>
              <Text style={styles.prodTitle}>
                {mainProduct.brand} {mainProduct.name}
              </Text>
              <Muted>Ihr Preis: {euro(unitPrice)}/kg</Muted>
              {tier && tier.price < basePrice ? (
                <Text testID="tier-active" style={styles.tierActive}>
                  Mengenrabatt aktiv · ab {num(tier.minQty)} kg statt {euro(basePrice)}
                </Text>
              ) : mainProduct.discountTiers?.length ? (
                <Muted>Mengenrabatt ab {num(mainProduct.discountTiers[0].minQty)} kg</Muted>
              ) : null}

              <View style={styles.stepper}>
                <Pressable
                  testID="qty-minus"
                  style={styles.stepBtn}
                  onPress={() => setQty((v) => Math.max(1, v - 1))}
                >
                  <Minus size={18} color={colors.onSurface} weight="bold" />
                </Pressable>
                <View style={styles.qtyBox}>
                  <Text style={styles.qtyValue}>{qty}</Text>
                  <Text style={styles.qtyUnit}>kg</Text>
                </View>
                <Pressable testID="qty-plus" style={styles.stepBtn} onPress={() => setQty((v) => v + 1)}>
                  <Plus size={18} color={colors.onSurface} weight="bold" />
                </Pressable>
              </View>

              <View style={styles.totalRow}>
                <Text style={styles.totalLabel}>Netto gesamt</Text>
                <Text style={styles.totalValue}>{euro(qty * unitPrice)}</Text>
              </View>

              <Button
                testID="submit-order"
                title="Bestellung absenden"
                loading={create.isPending}
                onPress={() => create.mutate()}
              />
            </Card>
          )}

          <SectionTitle style={{ marginTop: 4 }}>Bestellhistorie</SectionTitle>
          {(orders.data ?? []).length === 0 ? (
            <EmptyState title="Noch keine Bestellungen" subtitle="Ihre Bestellungen erscheinen hier" />
          ) : (
            (orders.data ?? []).map((o: any) => {
              const total = o.items.reduce((a: number, i: any) => a + i.price * i.qty, 0);
              return (
                <Pressable key={o.id} testID={`order-${o.id}`} onPress={() => router.push(`/bestellung/${o.id}`)}>
                  <Card>
                    <View style={styles.orderTop}>
                      <Text style={styles.prodTitle}>{o.id}</Text>
                      <StatusBadge status={o.status} />
                    </View>
                    <Muted>
                      {dateDE(o.createdAt)} · {num(o.items[0]?.qty)} kg · {euro(total)} netto
                    </Muted>
                  </Card>
                </Pressable>
              );
            })
          )}
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
}));
