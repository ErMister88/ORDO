import { useState } from "react";
import { View, Text, ScrollView, Pressable, Alert } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { ArrowLeft, Check, Truck, XCircle, ImageSquare, Receipt, Export } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet, apiPut, apiPost, fileUrl } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { shareInvoicePdf, shareDeliveryNotePdf } from "@/src/lib/pdf";
import { Card, InfoRow, Button, StatusBadge, Muted } from "@/src/components/ui";

const FLOW = ["Neu", "Bestätigt", "Kommissioniert", "Versendet", "Abgeschlossen"];

export default function BestellungDetail() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const qc = useQueryClient();
  const { user } = useAuth();
  const { id } = useLocalSearchParams<{ id: string }>();
  const isStaff = user?.role === "admin" || user?.role === "sales";
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelErr, setCancelErr] = useState("");

  const order = useQuery({ queryKey: ["order", id], queryFn: () => apiGet(`/orders/${id}`) });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });

  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));

  const o = order.data;
  const company = useQuery({
    queryKey: ["company", o?.companyId],
    queryFn: () => apiGet(`/companies/${o.companyId}`),
    enabled: !!o?.companyId,
  });
  const cancelled = o?.status === "Storniert";
  const currentIdx = o ? FLOW.indexOf(o.status) : -1;
  const total = o ? o.items.reduce((a: number, i: any) => a + i.price * i.qty, 0) : 0;

  const advance = useMutation({
    mutationFn: (status: string) => apiPut(`/orders/${id}/status`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["order", id] });
      qc.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  const createInvoice = useMutation({
    mutationFn: () => apiPost(`/orders/${id}/invoice`, {}),
    onSuccess: (inv: any) => {
      qc.invalidateQueries({ queryKey: ["order", id] });
      qc.invalidateQueries({ queryKey: ["invoices"] });
      Alert.alert("Rechnung erstellt", `Rechnung ${inv.id} wurde erstellt (brutto ${inv.amount.toFixed(2)} €).`);
    },
    onError: (e: any) => Alert.alert("Rechnung", e.message || "Fehler"),
  });

  const cancel = useMutation({
    mutationFn: () => apiPut(`/orders/${id}/cancel`, {}),
    onSuccess: () => {
      setConfirmCancel(false);
      setCancelErr("");
      qc.invalidateQueries({ queryKey: ["order", id] });
      qc.invalidateQueries({ queryKey: ["orders"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (e: any) => setCancelErr(e.message),
  });

  const nextStatus = currentIdx >= 0 && currentIdx < FLOW.length - 1 ? FLOW[currentIdx + 1] : null;

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>{id}</Text>
          <Text style={styles.subtitle}>Bestelldetails</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {!o ? (
          <Muted>Lädt…</Muted>
        ) : (
          <>
            {cancelled ? (
              <Card testID="order-cancelled-card">
                <View style={styles.statusHead}>
                  <Text style={styles.cardTitle}>Storniert</Text>
                  <XCircle size={22} color={colors.error} weight="fill" />
                </View>
                <Muted>
                  Diese Bestellung wurde storniert
                  {o.cancelledAt ? ` am ${dateDE(o.cancelledAt)}` : ""}.
                </Muted>
              </Card>
            ) : (
              <Card testID="order-status-card">
                <View style={styles.statusHead}>
                  <Text style={styles.cardTitle}>Lieferstatus</Text>
                  <StatusBadge status={o.status} />
                </View>
                <View style={styles.timeline}>
                  {FLOW.map((step, i) => {
                    const done = i < currentIdx;
                    const active = i === currentIdx;
                    const reached = i <= currentIdx;
                    return (
                      <View key={step} style={styles.tlRow}>
                        <View style={styles.tlLeft}>
                          <View
                            style={[
                              styles.tlDot,
                              reached && { backgroundColor: colors.brandPrimary, borderColor: colors.brandPrimary },
                              active && { backgroundColor: colors.success, borderColor: colors.success },
                            ]}
                          >
                            {done ? (
                              <Check size={12} color={colors.onBrandPrimary} weight="bold" />
                            ) : active ? (
                              <Truck size={12} color={colors.onSuccess} weight="bold" />
                            ) : null}
                          </View>
                          {i < FLOW.length - 1 && (
                            <View style={[styles.tlLine, i < currentIdx && { backgroundColor: colors.brandPrimary }]} />
                          )}
                        </View>
                        <Text style={[styles.tlLabel, reached && styles.tlLabelActive]}>{step}</Text>
                      </View>
                    );
                  })}
                </View>

                {isStaff && nextStatus && (
                  <Button
                    testID="advance-status"
                    title={`Status → ${nextStatus}`}
                    loading={advance.isPending}
                    onPress={() => advance.mutate(nextStatus)}
                    style={{ marginTop: 8 }}
                  />
                )}

                {o.status === "Neu" && (
                  <View style={styles.cancelZone}>
                    <Muted>Solange die Bestellung noch nicht bearbeitet wird, könnt ihr sie stornieren.</Muted>
                    {cancelErr ? <Text style={[styles.deliveredTxt, { color: colors.error }]}>{cancelErr}</Text> : null}
                    {confirmCancel ? (
                      <View style={styles.cancelRow}>
                        <Button
                          testID="confirm-cancel"
                          title="Ja, stornieren"
                          kind="danger"
                          style={{ flex: 1 }}
                          loading={cancel.isPending}
                          onPress={() => cancel.mutate()}
                        />
                        <Button
                          testID="abort-cancel"
                          title="Zurück"
                          kind="secondary"
                          style={{ flex: 1 }}
                          onPress={() => setConfirmCancel(false)}
                        />
                      </View>
                    ) : (
                      <Button
                        testID="cancel-order"
                        title="Bestellung stornieren"
                        kind="danger"
                        onPress={() => {
                          setCancelErr("");
                          setConfirmCancel(true);
                        }}
                        style={{ marginTop: 4 }}
                      />
                    )}
                  </View>
                )}
              </Card>
            )}

            {o.trackingNumber ? (
              <Card testID="order-shipping-card">
                <View style={styles.statusHead}>
                  <Text style={styles.cardTitle}>Versand</Text>
                  <Truck size={20} color={colors.brandPrimary} weight="bold" />
                </View>
                <InfoRow label="Sendungsnummer" value={o.trackingNumber} />
                {o.estimatedDelivery ? (
                  <InfoRow label="Voraussichtliche Lieferung" value={dateDE(o.estimatedDelivery)} />
                ) : null}
                {o.status === "Abgeschlossen" ? (
                  <Text style={[styles.deliveredTxt, { color: colors.success }]}>Zugestellt</Text>
                ) : (
                  <Muted style={{ marginTop: 4 }}>Ihre Bestellung ist unterwegs.</Muted>
                )}
              </Card>
            ) : null}

            <Card testID="order-items-card">
              <Text style={styles.cardTitle}>Positionen</Text>
              {o.items.map((it: any, idx: number) => {
                const p = prodMap[it.productId];
                return (
                  <View key={idx} style={[styles.itemRow, idx > 0 && styles.itemBorder]}>
                    {p?.imageUrl ? (
                      <Image source={{ uri: fileUrl(p.imageUrl) }} style={styles.itemThumb} contentFit="cover" transition={150} />
                    ) : (
                      <View style={[styles.itemThumb, styles.itemThumbEmpty]}>
                        <ImageSquare size={20} color={colors.muted} weight="duotone" />
                      </View>
                    )}
                    <View style={{ flex: 1 }}>
                      <Text style={styles.itemName}>{p ? `${p.brand} ${p.name}` : it.productId}</Text>
                      <Muted>
                        {num(it.qty)} kg × {euro(it.price)}
                      </Muted>
                    </View>
                    <Text style={styles.itemTotal}>{euro(it.price * it.qty)}</Text>
                  </View>
                );
              })}
              <View style={styles.divider} />
              <InfoRow label="Datum" value={dateDE(o.createdAt)} />
              <View style={styles.totalRow}>
                <Text style={styles.totalLabel}>Gesamt netto</Text>
                <Text style={styles.totalValue}>{euro(total)}</Text>
              </View>
            </Card>

            {o.customerNote ? (
              <Card testID="order-note-card">
                <Text style={styles.cardTitle}>Notiz des Kunden</Text>
                <Muted>{o.customerNote}</Muted>
              </Card>
            ) : null}

            <Card testID="order-docs-card">
              <Text style={styles.cardTitle}>Dokumente</Text>
              {(o.status === "Versendet" || o.status === "Abgeschlossen") && (
                <Pressable testID="delivery-note" style={styles.docBtn} onPress={() => shareDeliveryNotePdf(o, company.data, prodMap)}>
                  <Export size={16} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.docText}>Lieferschein als PDF</Text>
                </Pressable>
              )}
              {isStaff && (
                o.invoiceId ? (
                  <Muted testID="invoice-exists">Rechnung {o.invoiceId} erstellt.</Muted>
                ) : (
                  <Button
                    testID="create-invoice"
                    title="Rechnung erstellen (mit MwSt)"
                    kind="secondary"
                    loading={createInvoice.isPending}
                    onPress={() => createInvoice.mutate()}
                    style={{ marginTop: 4 }}
                  />
                )
              )}
              {!isStaff && !o.invoiceId && (o.status === "Versendet" || o.status === "Abgeschlossen") && (
                <Muted>Ihre Rechnung wird nach Bearbeitung bereitgestellt.</Muted>
              )}
            </Card>
          </>
        )}
      </ScrollView>
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
  backBtn: { width: 44, height: 44, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 24, fontWeight: "800", color: c.onSurface, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 2 },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  statusHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  docBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 12, backgroundColor: c.brandTertiary, marginBottom: 8 },
  docText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  cardTitle: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  timeline: { marginTop: 4 },
  tlRow: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  tlLeft: { alignItems: "center", width: 24 },
  tlDot: {
    width: 24,
    height: 24,
    borderRadius: 999,
    borderWidth: 2,
    borderColor: c.border,
    backgroundColor: c.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  tlLine: { width: 2, height: 22, backgroundColor: c.border, marginVertical: 2 },
  tlLabel: { fontSize: 14, color: c.muted, fontWeight: "600", paddingTop: 2 },
  tlLabelActive: { color: c.onSurface, fontWeight: "800" },
  itemRow: { flexDirection: "row", alignItems: "center", paddingVertical: 10, gap: 12 },
  itemThumb: { width: 44, height: 44, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  itemThumbEmpty: { alignItems: "center", justifyContent: "center" },
  itemBorder: { borderTopWidth: 1, borderTopColor: c.divider },
  itemName: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  itemTotal: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  divider: { height: 1, backgroundColor: c.divider, marginVertical: 6 },
  totalRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingTop: 4 },
  totalLabel: { fontSize: 15, color: c.muted, fontWeight: "600" },
  totalValue: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  deliveredTxt: { fontSize: 14, fontWeight: "800", marginTop: 4 },
  cancelZone: { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: c.divider, gap: 8 },
  cancelRow: { flexDirection: "row", gap: 10 },
}));
