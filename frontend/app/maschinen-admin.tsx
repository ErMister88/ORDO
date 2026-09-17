import { useState } from "react";
import { View, Text, ScrollView, Pressable, Alert, KeyboardAvoidingView, Platform } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Image } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import { ArrowLeft, Plus, PencilSimple, Trash, ImageSquare, Camera } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost, apiPut, apiDelete, apiUpload, fileUrl } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { Card, Input, Button, SectionTitle, Muted, EmptyState, StatusBadge, InfoRow } from "@/src/components/ui";

const EMPTY = { name: "", description: "", price: "", imageUrl: "", active: true };

export default function MaschinenAdmin() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const machines = useQuery({ queryKey: ["machines"], queryFn: () => apiGet("/machines") });
  const requests = useQuery({ queryKey: ["machine-requests"], queryFn: () => apiGet("/machine-requests") });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const leasingContracts = useQuery({ queryKey: ["leasing-contracts"], queryFn: () => apiGet("/machines/leasing-contracts") });

  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<any>({ ...EMPTY });
  const [uploading, setUploading] = useState(false);
  const set = (k: string) => (v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  const num = (v: string) => Number((v || "").replace(",", "."));

  const startNew = () => { setEditing("new"); setForm({ ...EMPTY }); };
  const startEdit = (m: any) => {
    setEditing(m.id);
    setForm({ name: m.name, description: m.description ?? "", price: String(m.price), imageUrl: m.imageUrl ?? "", active: m.active });
  };

  const pickImage = async () => {
    let perm = await ImagePicker.getMediaLibraryPermissionsAsync();
    if (perm.status === "undetermined" || (perm.status === "denied" && perm.canAskAgain)) {
      perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    }
    if (perm.status !== "granted") { Alert.alert("Zugriff nötig", "Bitte Foto-Zugriff in den Einstellungen erlauben."); return; }
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.7, allowsEditing: true, aspect: [4, 3] });
    if (res.canceled || !res.assets?.length) return;
    const asset = res.assets[0];
    setUploading(true);
    try {
      const name = asset.fileName || `maschine.${(asset.uri.split(".").pop() || "jpg").split("?")[0]}`;
      const up = await apiUpload(asset.uri, name, asset.mimeType || "image/jpeg");
      setForm((f: any) => ({ ...f, imageUrl: up.url }));
    } catch (e: any) {
      Alert.alert("Upload fehlgeschlagen", e.message || "");
    } finally {
      setUploading(false);
    }
  };

  const save = useMutation({
    mutationFn: () => {
      const body = { name: form.name, description: form.description, imageUrl: form.imageUrl, price: num(form.price), active: form.active };
      return editing === "new" ? apiPost("/machines", body) : apiPut(`/machines/${editing}`, body);
    },
    onSuccess: () => { setEditing(null); qc.invalidateQueries({ queryKey: ["machines"] }); },
    onError: (e: any) => Alert.alert("Fehler", e.message || "Speichern fehlgeschlagen"),
  });

  const del = useMutation({
    mutationFn: (id: string) => apiDelete(`/machines/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["machines"] }),
  });

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} testID="back-button" hitSlop={10}>
          <ArrowLeft size={24} color={colors.onSurface} weight="bold" />
        </Pressable>
        <Text style={styles.headerTitle}>Maschinen-Verwaltung</Text>
        <Pressable onPress={startNew} testID="new-machine" hitSlop={10}>
          <Plus size={24} color={colors.brandPrimary} weight="bold" />
        </Pressable>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={{ padding: 16, gap: 12, paddingBottom: insets.bottom + 32 }} keyboardShouldPersistTaps="handled">
          {editing ? (
            <Card testID="machine-form">
              <SectionTitle>{editing === "new" ? "Neue Maschine" : "Maschine bearbeiten"}</SectionTitle>
              <Pressable onPress={pickImage} style={styles.imgPick} testID="pick-image">
                {fileUrl(form.imageUrl) ? (
                  <Image source={{ uri: fileUrl(form.imageUrl) }} style={styles.imgPickImg} contentFit="cover" />
                ) : (
                  <View style={styles.imgPickPh}>
                    <Camera size={26} color={colors.muted} />
                    <Muted>{uploading ? "Lädt…" : "Bild auswählen"}</Muted>
                  </View>
                )}
              </Pressable>
              <Text style={styles.label}>Name</Text>
              <Input testID="m-name" value={form.name} onChangeText={set("name")} placeholder="z. B. WMF 1500 S+" />
              <Text style={styles.label}>Beschreibung</Text>
              <Input testID="m-desc" value={form.description} onChangeText={set("description")} placeholder="Kurzbeschreibung" multiline style={{ minHeight: 60 }} />
              <Text style={styles.label}>Preis (brutto, inkl. 19% MwSt)</Text>
              <Input testID="m-price" value={form.price} onChangeText={set("price")} keyboardType="decimal-pad" placeholder="0,00" />
              <Pressable style={styles.switchRow} onPress={() => set("active")(!form.active)} testID="m-active">
                <View style={[styles.checkbox, form.active && styles.checkboxOn]} />
                <Text style={styles.switchTxt}>Aktiv (im Katalog sichtbar)</Text>
              </Pressable>
              <View style={styles.rowGap}>
                <Button testID="m-save" title="Speichern" loading={save.isPending} onPress={() => save.mutate()} style={{ flex: 1 }} />
                <Button title="Abbrechen" kind="secondary" onPress={() => setEditing(null)} style={{ flex: 1 }} />
              </View>
            </Card>
          ) : null}

          <SectionTitle>Katalog ({machines.data?.length ?? 0})</SectionTitle>
          {(machines.data ?? []).map((m: any) => (
            <Card key={m.id} testID={`adm-machine-${m.id}`}>
              <View style={styles.mRow}>
                {fileUrl(m.imageUrl) ? (
                  <Image source={{ uri: fileUrl(m.imageUrl) }} style={styles.mImg} contentFit="cover" />
                ) : (
                  <View style={[styles.mImg, styles.mImgPh]}><ImageSquare size={24} color={colors.muted} /></View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.mName}>{m.name}</Text>
                  <Text style={styles.mPrice}>{euro(m.price)}</Text>
                  {!m.active ? <Muted>Inaktiv</Muted> : null}
                </View>
              </View>
              <View style={styles.rowGap}>
                <Pressable style={styles.iconBtn} onPress={() => startEdit(m)} testID={`edit-${m.id}`}>
                  <PencilSimple size={18} color={colors.brandPrimary} />
                  <Text style={styles.iconTxt}>Bearbeiten</Text>
                </Pressable>
                <Pressable style={styles.iconBtn} onPress={() => Alert.alert("Löschen?", `${m.name} deaktivieren?`, [{ text: "Abbrechen" }, { text: "Löschen", style: "destructive", onPress: () => del.mutate(m.id) }])} testID={`del-${m.id}`}>
                  <Trash size={18} color={colors.error} />
                  <Text style={[styles.iconTxt, { color: colors.error }]}>Deaktivieren</Text>
                </Pressable>
              </View>
            </Card>
          ))}

          <SectionTitle style={{ marginTop: 8 }}>Anfragen ({requests.data?.length ?? 0})</SectionTitle>
          {(requests.data ?? []).length === 0 ? (
            <EmptyState title="Keine Anfragen" subtitle="Finanzierungs-/Leasing-Anfragen erscheinen hier." />
          ) : (
            (requests.data ?? []).map((r: any) => (
              <RequestCard
                key={r.id}
                r={r}
                products={products.data ?? []}
                onDone={() => {
                  qc.invalidateQueries({ queryKey: ["machine-requests"] });
                  qc.invalidateQueries({ queryKey: ["leasing-contracts"] });
                }}
              />
            ))
          )}

          <SectionTitle style={{ marginTop: 8 }}>Leasing-Kaffeeverträge ({leasingContracts.data?.length ?? 0})</SectionTitle>
          {(leasingContracts.data ?? []).length === 0 ? (
            <EmptyState title="Keine Leasing-Verträge" subtitle="Aus Leasing-Zusagen entstehen hier automatisch Kaffeelieferverträge." />
          ) : (
            (leasingContracts.data ?? []).map((ct: any) => (
              <Card key={ct.id} testID={`lease-contract-${ct.id}`}>
                <Text style={styles.mName}>{ct.id}</Text>
                <Muted>{ct.companyName || "—"}</Muted>
                <View style={{ marginTop: 8 }}>
                  <InfoRow label="Maschine" value={ct.machine || "—"} />
                  <InfoRow label="Leasingrate" value={`${euro(ct.machineRate)}/Monat`} />
                  <InfoRow label="Kaffee" value={ct.productName || "—"} />
                  <InfoRow label="Kaffeepreis" value={`${euro(ct.price)}/kg`} />
                  <InfoRow label="Mindestabnahme" value={`${ct.minQtyMonth} kg/Monat`} />
                  <InfoRow label="Laufzeit" value={`${ct.termMonths} Monate`} />
                </View>
              </Card>
            ))
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function RequestCard({ r, products, onDone }: { r: any; products: any[]; onDone: () => void }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const isLease = r.type === "leasing";
  const isBuy = r.type === "kauf";
  const [t, setT] = useState<any>({
    downPayment: r.terms?.downPayment != null ? String(r.terms.downPayment) : "",
    monthlyRate: r.terms?.monthlyRate != null ? String(r.terms.monthlyRate) : "",
    finalPayment: r.terms?.finalPayment != null ? String(r.terms.finalPayment) : "",
    termMonths: String(r.terms?.termMonths ?? r.termMonths ?? 48),
    minCoffeeKgMonth: r.terms?.minCoffeeKgMonth != null ? String(r.terms.minCoffeeKgMonth) : "",
    productId: r.terms?.productId ?? "",
    coffeePricePerKg: r.terms?.coffeePricePerKg != null ? String(r.terms.coffeePricePerKg) : "",
    note: r.terms?.note ?? "",
  });
  const set = (k: string) => (v: string) => setT((s: any) => ({ ...s, [k]: v }));
  const num = (v: string) => (v === "" ? null : Number((v || "").replace(",", ".")));

  const send = useMutation({
    mutationFn: () => apiPut(`/machine-requests/${r.id}`, {
      status: "Angebot",
      downPayment: num(t.downPayment), monthlyRate: num(t.monthlyRate), finalPayment: num(t.finalPayment),
      termMonths: Number(t.termMonths) || 48,
      minCoffeeKgMonth: isLease ? num(t.minCoffeeKgMonth) : null,
      productId: isLease ? (t.productId || null) : null,
      coffeePricePerKg: isLease ? num(t.coffeePricePerKg) : null,
      note: t.note,
    }),
    onSuccess: () => { onDone(); Alert.alert("Angebot gesendet", "Der Kunde wurde per E-Mail informiert."); },
    onError: (e: any) => Alert.alert("Fehler", e.message || "Konnte nicht gesendet werden"),
  });

  const submit = () => {
    if (isLease) {
      if (!num(t.minCoffeeKgMonth) || Number(num(t.minCoffeeKgMonth)) <= 0) {
        Alert.alert("Kaffeebindung nötig", "Bitte eine Kaffee-Mindestabnahme größer 0 angeben."); return;
      }
      if (!t.productId) { Alert.alert("Kaffeesorte wählen", "Bitte eine Kaffeesorte für die Bindung wählen."); return; }
      if (!num(t.coffeePricePerKg) || Number(num(t.coffeePricePerKg)) <= 0) {
        Alert.alert("Kaffeepreis nötig", "Bitte einen Kaffeepreis pro kg angeben."); return;
      }
    }
    send.mutate();
  };

  const label = isBuy ? "Kauf" : isLease ? "Leasing (Kaffeebindung)" : "Finanzierung";

  return (
    <Card testID={`adm-req-${r.id}`}>
      <View style={styles.reqTop}>
        <Text style={styles.mName}>{r.id}</Text>
        <StatusBadge status={r.status} />
      </View>
      <Text style={styles.reqMachine}>{r.machineName} · {euro(r.machinePrice)}</Text>
      <Muted>{label} · {r.customer?.companyName || r.customer?.userName} · {r.customer?.email}</Muted>
      {r.message ? <Muted style={{ marginTop: 4 }}>„{r.message}"</Muted> : null}

      {(r.questions ?? []).length > 0 ? (
        <View style={styles.qBox}>
          <Text style={styles.qHead}>Rückfragen des Kunden</Text>
          {(r.questions ?? []).map((q: any, i: number) => (
            <Text key={i} style={styles.qText}>• {q.message}</Text>
          ))}
        </View>
      ) : null}

      {!isBuy ? (
        <View style={styles.termsForm}>
          <Text style={styles.label}>Anzahlung (€)</Text>
          <Input testID={`t-down-${r.id}`} value={t.downPayment} onChangeText={set("downPayment")} keyboardType="decimal-pad" placeholder="z. B. 500" />
          <Text style={styles.label}>Monatliche Rate (€)</Text>
          <Input testID={`t-rate-${r.id}`} value={t.monthlyRate} onChangeText={set("monthlyRate")} keyboardType="decimal-pad" placeholder="z. B. 149,90" />
          <Text style={styles.label}>Laufzeit (Monate)</Text>
          <Input testID={`t-term-${r.id}`} value={t.termMonths} onChangeText={set("termMonths")} keyboardType="number-pad" placeholder="48" />
          <Text style={styles.label}>Schlussrate / Übernahme (€)</Text>
          <Input testID={`t-final-${r.id}`} value={t.finalPayment} onChangeText={set("finalPayment")} keyboardType="decimal-pad" placeholder="z. B. 1200" />
          {isLease ? (
            <>
              <Text style={styles.label}>Kaffee-Mindestabnahme (kg/Monat) *</Text>
              <Input testID={`t-coffee-${r.id}`} value={t.minCoffeeKgMonth} onChangeText={set("minCoffeeKgMonth")} keyboardType="decimal-pad" placeholder="z. B. 10" />
              <Text style={styles.label}>Kaffeesorte *</Text>
              <View style={styles.coffeeChips}>
                {products.filter((p: any) => p.active !== false).map((p: any) => {
                  const active = t.productId === p.id;
                  return (
                    <Pressable key={p.id} testID={`t-prod-${r.id}-${p.id}`} onPress={() => set("productId")(p.id)} style={[styles.coffeeChip, active && styles.coffeeChipActive]}>
                      <Text style={[styles.coffeeChipTxt, active && styles.coffeeChipTxtActive]}>{p.brand} {p.name}</Text>
                    </Pressable>
                  );
                })}
              </View>
              <Text style={styles.label}>Kaffeepreis (€/kg) *</Text>
              <Input testID={`t-cprice-${r.id}`} value={t.coffeePricePerKg} onChangeText={set("coffeePricePerKg")} keyboardType="decimal-pad" placeholder="z. B. 15,90" />
            </>
          ) : null}
          <Text style={styles.label}>Notiz (optional)</Text>
          <Input testID={`t-note-${r.id}`} value={t.note} onChangeText={set("note")} placeholder="z. B. inkl. Wartung" multiline style={{ minHeight: 50 }} />
          <Button testID={`send-offer-${r.id}`} title="Angebot senden" loading={send.isPending} onPress={submit} style={{ marginTop: 10 }} />
        </View>
      ) : (
        <Muted style={{ marginTop: 6 }}>Zahlungsstatus: {r.paymentStatus}</Muted>
      )}
    </Card>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingBottom: 12, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.border },
  headerTitle: { fontSize: 18, fontWeight: "800", color: c.onSurface },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 10, marginBottom: 4 },
  imgPick: { height: 150, borderRadius: 12, backgroundColor: c.surfaceTertiary, overflow: "hidden", marginTop: 8 },
  imgPickImg: { width: "100%", height: "100%" },
  imgPickPh: { flex: 1, alignItems: "center", justifyContent: "center", gap: 6 },
  switchRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 12 },
  checkbox: { width: 22, height: 22, borderRadius: 6, borderWidth: 2, borderColor: c.border },
  checkboxOn: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  switchTxt: { fontSize: 14, fontWeight: "600", color: c.onSurface },
  rowGap: { flexDirection: "row", gap: 8, marginTop: 12 },
  mRow: { flexDirection: "row", gap: 12 },
  mImg: { width: 70, height: 70, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  mImgPh: { alignItems: "center", justifyContent: "center" },
  mName: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  mPrice: { fontSize: 15, fontWeight: "800", color: c.brandPrimary, marginTop: 4 },
  iconBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  iconTxt: { fontSize: 13, fontWeight: "700", color: c.brandPrimary },
  reqTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  reqMachine: { fontSize: 15, fontWeight: "700", color: c.onSurface, marginTop: 6 },
  termsForm: { marginTop: 10, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 8 },
  coffeeChips: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 2 },
  coffeeChip: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 999, backgroundColor: c.surfaceTertiary, borderWidth: 1, borderColor: c.border },
  coffeeChipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  coffeeChipTxt: { fontSize: 12, fontWeight: "700", color: c.onSurfaceSecondary },
  coffeeChipTxtActive: { color: c.onBrandPrimary },
  qBox: { marginTop: 8, padding: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  qHead: { fontSize: 12, fontWeight: "800", color: c.onSurfaceSecondary, marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.5 },
  qText: { fontSize: 13, color: c.onSurface, lineHeight: 19 },
}));
