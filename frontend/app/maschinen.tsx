import { useState } from "react";
import { View, Text, ScrollView, Pressable, Alert } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Image } from "expo-image";
import * as WebBrowser from "expo-web-browser";
import { ArrowLeft, Coffee, CurrencyEur, ImageSquare, Receipt, FileText } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost, fileUrl } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { shareMachineContractPdf, shareMachineInvoicePdf } from "@/src/lib/pdf";
import { Card, Button, Input, SectionTitle, Muted, EmptyState, InfoRow, StatusBadge } from "@/src/components/ui";

const TERMS = [24, 36, 48];

export default function Maschinen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const machines = useQuery({ queryKey: ["machines"], queryFn: () => apiGet("/machines") });
  const requests = useQuery({ queryKey: ["machine-requests"], queryFn: () => apiGet("/machine-requests") });

  const [panel, setPanel] = useState<{ id: string; type: "finanzierung" | "leasing"; term: number } | null>(null);
  const [payingId, setPayingId] = useState<string | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["machine-requests"] });
  };

  const requestOffer = useMutation({
    mutationFn: (b: { machineId: string; type: string; termMonths: number }) => apiPost("/machine-requests", b),
    onSuccess: () => {
      setPanel(null);
      refresh();
      Alert.alert("Anfrage gesendet", "Wir erstellen Ihr persönliches Angebot und melden uns in Kürze.");
    },
    onError: (e: any) => Alert.alert("Fehler", e.message || "Anfrage fehlgeschlagen"),
  });

  const accept = useMutation({
    mutationFn: (id: string) => apiPost(`/machine-requests/${id}/accept`, {}),
    onSuccess: () => { refresh(); Alert.alert("Angebot angenommen", "Vielen Dank! Wir setzen uns mit Ihnen in Verbindung."); },
    onError: (e: any) => Alert.alert("Fehler", e.message || "Konnte nicht angenommen werden"),
  });

  const respond = useMutation({
    mutationFn: (b: { id: string; action: string; message?: string }) =>
      apiPost(`/machine-requests/${b.id}/respond`, { action: b.action, message: b.message ?? "" }),
    onSuccess: () => { setQ(null); refresh(); },
    onError: (e: any) => Alert.alert("Fehler", e.message || "Aktion fehlgeschlagen"),
  });
  const [q, setQ] = useState<{ id: string; text: string } | null>(null);

  const buy = async (machineId: string) => {
    setPayingId(machineId);
    try {
      const req = await apiPost("/machine-requests", { machineId, type: "kauf" });
      const res = await apiPost(`/machine-requests/${req.id}/checkout`, {});
      if (res?.url) {
        await WebBrowser.openBrowserAsync(res.url);
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 1500));
          const st = await apiGet(`/machine-requests/${req.id}/payment-status`);
          if (st.status === "Bezahlt") break;
        }
      }
      refresh();
    } catch (e: any) {
      Alert.alert("Zahlung nicht möglich", e.message || "Die Kartenzahlung ist erst nach Veröffentlichung der App aktiv.");
      refresh();
    } finally {
      setPayingId(null);
    }
  };

  const payExisting = async (reqId: string) => {
    setPayingId(reqId);
    try {
      const res = await apiPost(`/machine-requests/${reqId}/checkout`, {});
      if (res?.url) {
        await WebBrowser.openBrowserAsync(res.url);
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 1500));
          const st = await apiGet(`/machine-requests/${reqId}/payment-status`);
          if (st.status === "Bezahlt") break;
        }
      }
      refresh();
    } catch (e: any) {
      Alert.alert("Zahlung nicht möglich", e.message || "Die Kartenzahlung ist erst nach Veröffentlichung aktiv.");
    } finally {
      setPayingId(null);
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} testID="back-button" hitSlop={10}>
          <ArrowLeft size={24} color={colors.onSurface} weight="bold" />
        </Pressable>
        <Text style={styles.headerTitle}>Maschinen</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: 16, gap: 12, paddingBottom: insets.bottom + 32 }}>
        <Muted>Kaufen Sie Ihre Maschine direkt oder fordern Sie ein Finanzierungs- bzw. Leasing-Angebot (mit Kaffeebindung) an.</Muted>

        <SectionTitle>Katalog</SectionTitle>
        {machines.isLoading ? (
          <Muted>Lädt…</Muted>
        ) : (machines.data ?? []).length === 0 ? (
          <EmptyState title="Keine Maschinen" subtitle="Aktuell sind keine Maschinen verfügbar." />
        ) : (
          (machines.data ?? []).map((m: any) => (
            <Card key={m.id} testID={`machine-${m.id}`}>
              <View style={styles.mRow}>
                {fileUrl(m.imageUrl) ? (
                  <Image source={{ uri: fileUrl(m.imageUrl) }} style={styles.mImg} contentFit="cover" />
                ) : (
                  <View style={[styles.mImg, styles.mImgPh]}>
                    <ImageSquare size={26} color={colors.muted} />
                  </View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.mName}>{m.name}</Text>
                  {m.description ? <Muted>{m.description}</Muted> : null}
                  <Text style={styles.mPrice}>{euro(m.price)} <Text style={styles.mVat}>inkl. 19% MwSt</Text></Text>
                </View>
              </View>

              <View style={styles.actions}>
                <Button
                  testID={`buy-${m.id}`}
                  title="Kaufen"
                  loading={payingId === m.id}
                  onPress={() => buy(m.id)}
                  style={{ flex: 1 }}
                />
                <Button
                  testID={`finance-${m.id}`}
                  title="Finanzieren"
                  kind="secondary"
                  onPress={() => setPanel({ id: m.id, type: "finanzierung", term: 48 })}
                  style={{ flex: 1 }}
                />
                <Button
                  testID={`lease-${m.id}`}
                  title="Leasing"
                  kind="secondary"
                  onPress={() => setPanel({ id: m.id, type: "leasing", term: 48 })}
                  style={{ flex: 1 }}
                />
              </View>

              {panel && panel.id === m.id ? (
                <View style={styles.panel}>
                  <View style={styles.panelHead}>
                    {panel.type === "leasing" ? <Coffee size={18} color={colors.brandPrimary} weight="fill" /> : <CurrencyEur size={18} color={colors.brandPrimary} weight="fill" />}
                    <Text style={styles.panelTitle}>
                      {panel.type === "leasing" ? "Leasing mit Kaffeebindung" : "Finanzierung anfragen"}
                    </Text>
                  </View>
                  {panel.type === "leasing" ? (
                    <Muted>Monatliche Leasingrate + Mindestabnahme Kaffee. Übernahme am Laufzeitende möglich.</Muted>
                  ) : (
                    <Muted>Wir erstellen Ihnen ein individuelles Finanzierungsangebot (Anzahlung, Rate, Schlussrate).</Muted>
                  )}
                  <Text style={styles.panelLabel}>Laufzeit</Text>
                  <View style={styles.chips}>
                    {TERMS.map((t) => {
                      const active = panel.term === t;
                      return (
                        <Pressable
                          key={t}
                          testID={`term-${m.id}-${t}`}
                          onPress={() => setPanel({ ...panel, term: t })}
                          style={[styles.chip, active && styles.chipActive]}
                        >
                          <Text style={[styles.chipTxt, active && styles.chipTxtActive]}>{t} Monate</Text>
                        </Pressable>
                      );
                    })}
                  </View>
                  <View style={styles.calc} testID={`calc-${m.id}`}>
                    <Text style={styles.calcLabel}>Beispiel-Monatsrate</Text>
                    <Text style={styles.calcValue}>ca. {euro(m.price / panel.term)}</Text>
                    <Text style={styles.calcNote}>{m.price > 0 ? `${euro(m.price)} ÷ ${panel.term} Monate` : ""} · unverbindlich, ohne Anzahlung/Zins. Ihr individuelles Angebot erhalten Sie vom Team.</Text>
                  </View>
                  <View style={styles.actions}>
                    <Button
                      testID={`submit-request-${m.id}`}
                      title="Angebot anfordern"
                      loading={requestOffer.isPending}
                      onPress={() => requestOffer.mutate({ machineId: m.id, type: panel.type, termMonths: panel.term })}
                      style={{ flex: 1 }}
                    />
                    <Button title="Abbrechen" kind="secondary" onPress={() => setPanel(null)} style={{ flex: 1 }} />
                  </View>
                </View>
              ) : null}
            </Card>
          ))
        )}

        <SectionTitle style={{ marginTop: 8 }}>Meine Anfragen</SectionTitle>
        {(requests.data ?? []).length === 0 ? (
          <EmptyState title="Noch keine Anfragen" subtitle="Ihre Käufe und Angebote erscheinen hier." />
        ) : (
          (requests.data ?? []).map((r: any) => (
            <Card key={r.id} testID={`req-${r.id}`}>
              <View style={styles.reqTop}>
                <Text style={styles.reqId}>{r.id}</Text>
                <StatusBadge status={r.status} />
              </View>
              <Text style={styles.reqMachine}>{r.machineName}</Text>
              <Muted>{r.type === "kauf" ? "Kauf" : r.type === "finanzierung" ? "Finanzierung" : "Leasing (Kaffeebindung)"} · {euro(r.machinePrice)}</Muted>

              {r.terms ? (
                <View style={styles.termsBox}>
                  {r.terms.downPayment != null ? <InfoRow label="Anzahlung" value={euro(r.terms.downPayment)} /> : null}
                  {r.terms.monthlyRate != null ? <InfoRow label={`Monatsrate (${r.terms.termMonths} Mon.)`} value={euro(r.terms.monthlyRate)} /> : null}
                  {r.terms.finalPayment != null ? <InfoRow label="Schlussrate (Übernahme)" value={euro(r.terms.finalPayment)} /> : null}
                  {r.terms.coffeeName ? <InfoRow label="Kaffeesorte" value={r.terms.coffeeName} /> : null}
                  {r.terms.coffeePricePerKg != null ? <InfoRow label="Kaffeepreis" value={`${euro(r.terms.coffeePricePerKg)}/kg`} /> : null}
                  {r.terms.minCoffeeKgMonth != null ? <InfoRow label="Kaffee-Mindestabnahme" value={`${r.terms.minCoffeeKgMonth} kg / Monat`} /> : null}
                  {r.terms.note ? <Muted style={{ marginTop: 6 }}>{r.terms.note}</Muted> : null}
                </View>
              ) : null}

              {r.status === "Angebot" ? (
                <>
                  <Button testID={`accept-${r.id}`} title="Angebot annehmen" loading={accept.isPending} onPress={() => accept.mutate(r.id)} style={{ marginTop: 10 }} />
                  <View style={styles.actions}>
                    <Button
                      testID={`decline-${r.id}`}
                      title="Ablehnen"
                      kind="secondary"
                      onPress={() => Alert.alert("Angebot ablehnen?", "Möchten Sie dieses Angebot wirklich ablehnen?", [
                        { text: "Abbrechen" },
                        { text: "Ablehnen", style: "destructive", onPress: () => respond.mutate({ id: r.id, action: "decline" }) },
                      ])}
                      style={{ flex: 1 }}
                    />
                    <Button
                      testID={`question-${r.id}`}
                      title="Rückfrage"
                      kind="secondary"
                      onPress={() => setQ(q?.id === r.id ? null : { id: r.id, text: "" })}
                      style={{ flex: 1 }}
                    />
                  </View>
                  {q && q.id === r.id ? (
                    <View style={{ marginTop: 8 }}>
                      <Input
                        testID={`question-input-${r.id}`}
                        value={q.text}
                        onChangeText={(v) => setQ({ id: r.id, text: v })}
                        placeholder="Ihre Frage an unser Team…"
                        multiline
                        style={{ minHeight: 60 }}
                      />
                      <Button
                        testID={`question-send-${r.id}`}
                        title="Frage senden"
                        loading={respond.isPending}
                        onPress={() => {
                          if (!q.text.trim()) { Alert.alert("Frage fehlt", "Bitte eine Frage eingeben."); return; }
                          respond.mutate({ id: r.id, action: "question", message: q.text.trim() });
                        }}
                        style={{ marginTop: 8 }}
                      />
                    </View>
                  ) : null}
                </>
              ) : null}
              {r.status === "Bestätigt" && r.type !== "kauf" ? (
                <Pressable testID={`contract-pdf-${r.id}`} style={styles.pdfBtn} onPress={() => shareMachineContractPdf(r)}>
                  <FileText size={16} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.pdfText}>Vertrag als PDF</Text>
                </Pressable>
              ) : null}
              {r.type === "kauf" && r.paymentStatus === "Bezahlt" ? (
                <Pressable testID={`invoice-pdf-${r.id}`} style={styles.pdfBtn} onPress={() => shareMachineInvoicePdf(r)}>
                  <Receipt size={16} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.pdfText}>Kaufbeleg als PDF</Text>
                </Pressable>
              ) : null}
              {r.type === "kauf" && r.paymentStatus !== "Bezahlt" ? (
                <Button testID={`pay-${r.id}`} title="Jetzt bezahlen" loading={payingId === r.id} onPress={() => payExisting(r.id)} style={{ marginTop: 10 }} />
              ) : null}
            </Card>
          ))
        )}
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingBottom: 12, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.border },
  headerTitle: { fontSize: 18, fontWeight: "800", color: c.onSurface },
  mRow: { flexDirection: "row", gap: 12 },
  mImg: { width: 84, height: 84, borderRadius: 12, backgroundColor: c.surfaceTertiary },
  mImgPh: { alignItems: "center", justifyContent: "center" },
  mName: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  mPrice: { fontSize: 16, fontWeight: "800", color: c.brandPrimary, marginTop: 6 },
  mVat: { fontSize: 11, fontWeight: "600", color: c.muted },
  actions: { flexDirection: "row", gap: 8, marginTop: 12 },
  panel: { marginTop: 12, padding: 12, borderRadius: 12, backgroundColor: c.brandTertiary },
  panelHead: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 4 },
  panelTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  panelLabel: { fontSize: 12, fontWeight: "800", color: c.onSurfaceSecondary, marginTop: 10, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 999, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border },
  chipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  chipTxt: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary },
  chipTxtActive: { color: c.onBrandPrimary },
  reqTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  reqId: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  reqMachine: { fontSize: 15, fontWeight: "700", color: c.onSurface, marginTop: 6 },
  termsBox: { marginTop: 10, padding: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  calc: { marginTop: 12, padding: 12, borderRadius: 10, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border, alignItems: "center" },
  calcLabel: { fontSize: 12, fontWeight: "800", color: c.onSurfaceSecondary, textTransform: "uppercase", letterSpacing: 0.5 },
  calcValue: { fontSize: 24, fontWeight: "800", color: c.brandPrimary, marginTop: 2 },
  calcNote: { fontSize: 11, color: c.muted, textAlign: "center", marginTop: 4 },
  pdfBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 10, paddingVertical: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  pdfText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
}));
