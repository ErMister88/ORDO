import { useEffect, useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPut } from "@/src/api/client";
import { euro, dateDE } from "@/src/lib/format";
import { Card, Input, Button, SectionTitle, StatusBadge, EmptyState, Muted, InfoRow } from "@/src/components/ui";

export default function ShopAdmin() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const settings = useQuery({ queryKey: ["shop-settings"], queryFn: () => apiGet("/shop/settings") });
  const orders = useQuery({ queryKey: ["shop-orders"], queryFn: () => apiGet("/shop/orders") });

  const [thr, setThr] = useState("");
  const [fee, setFee] = useState("");
  useEffect(() => {
    if (settings.data) {
      setThr(String(settings.data.freeShippingThreshold));
      setFee(String(settings.data.shippingFee));
    }
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () =>
      apiPut("/shop/settings", {
        freeShippingThreshold: Number(thr.replace(",", ".")) || 0,
        shippingFee: Number(fee.replace(",", ".")) || 0,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shop-settings"] }),
  });

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} style={styles.iconBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Shop-Verwaltung</Text>
          <Text style={styles.subtitle}>Versand & B2C-Bestellungen</Text>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          <SectionTitle>Versandeinstellungen</SectionTitle>
          <Card>
            <Text style={styles.label}>Gratis-Versand ab (€)</Text>
            <Input testID="shop-threshold" value={thr} onChangeText={setThr} keyboardType="decimal-pad" />
            <Text style={styles.label}>Versandkosten sonst (€)</Text>
            <Input testID="shop-fee" value={fee} onChangeText={setFee} keyboardType="decimal-pad" />
            <Button testID="shop-settings-save" title="Speichern" loading={save.isPending} onPress={() => save.mutate()} style={{ marginTop: 10 }} />
          </Card>

          <SectionTitle style={{ marginTop: 4 }}>Shop-Bestellungen ({orders.data?.length ?? 0})</SectionTitle>
          {(orders.data ?? []).length === 0 ? (
            <EmptyState title="Noch keine Shop-Bestellungen" subtitle="B2C-Bestellungen erscheinen hier" />
          ) : (
            (orders.data ?? []).map((o: any) => (
              <Card key={o.id} testID={`shop-order-${o.id}`}>
                <View style={styles.rowTop}>
                  <Text style={styles.oId}>{o.id}</Text>
                  <StatusBadge status={o.paymentStatus} />
                </View>
                <Muted>
                  {dateDE(o.createdAt)} · {o.customer?.name} · {o.items.length} Artikel
                </Muted>
                <InfoRow label="Versand" value={o.shipping === 0 ? "Gratis" : euro(o.shipping)} />
                {o.taxTotal != null ? <InfoRow label="enthaltene MwSt" value={euro(o.taxTotal)} /> : null}
                <InfoRow label="Gesamt" value={euro(o.total)} />
                {o.customer?.street ? (
                  <Muted>
                    {o.customer.street}, {o.customer.zip} {o.customer.city}
                  </Muted>
                ) : null}
              </Card>
            ))
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingBottom: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  subtitle: { fontSize: 13, color: c.muted, marginTop: 1 },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  rowTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 4 },
  oId: { fontSize: 16, fontWeight: "800", color: c.onSurface },
}));
