import { useState } from "react";
import { View, Text, ScrollView, Pressable } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft, Phone, EnvelopeSimple, MapPin } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { Card, InfoRow, Button, StatusBadge, EmptyState, Muted } from "@/src/components/ui";

export default function KundeDetail() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [tab, setTab] = useState<"konditionen" | "historie">("konditionen");

  const company = useQuery({ queryKey: ["company", id], queryFn: () => apiGet(`/companies/${id}`) });
  const prices = useQuery({ queryKey: ["prices", id], queryFn: () => apiGet(`/companies/${id}/prices`) });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => apiGet("/orders") });

  const c = company.data;
  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));
  const custOrders = (orders.data ?? []).filter((o: any) => o.companyId === id);

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
          </Card>
        )}

        <View style={styles.segment}>
          {(["konditionen", "historie"] as const).map((t) => (
            <Pressable
              key={t}
              testID={`segment-${t}`}
              style={[styles.segItem, tab === t && styles.segItemActive]}
              onPress={() => setTab(t)}
            >
              <Text style={[styles.segText, tab === t && styles.segTextActive]}>
                {t === "konditionen" ? "Konditionen" : "Historie"}
              </Text>
            </Pressable>
          ))}
        </View>

        {tab === "konditionen" ? (
          (prices.data ?? []).length === 0 ? (
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
          )
        ) : custOrders.length === 0 ? (
          <EmptyState title="Keine Bestellungen" subtitle="Für diesen Kunden liegen keine Bestellungen vor" />
        ) : (
          custOrders.map((o: any) => {
            const total = o.items.reduce((a: number, i: any) => a + i.price * i.qty, 0);
            return (
              <Card key={o.id} testID={`order-${o.id}`}>
                <View style={styles.orderTop}>
                  <Text style={styles.prodTitle}>{o.id}</Text>
                  <StatusBadge status={o.status} />
                </View>
                <InfoRow label="Datum" value={dateDE(o.createdAt)} />
                <InfoRow label="Betrag" value={`${euro(total)} netto`} />
              </Card>
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
  orderTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  footer: {
    padding: 20,
    paddingTop: 12,
    backgroundColor: c.surface,
    borderTopWidth: 1,
    borderTopColor: c.divider,
  },
}));
