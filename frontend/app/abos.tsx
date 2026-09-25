import { useState } from "react";
import {
  View,
  ScrollView,
  Pressable,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft, CaretDown, Trash, ArrowClockwise } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost, apiPostIdempotent, apiPut, apiDelete } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { Card, Input, Button, SectionTitle, Muted, EmptyState } from "@/src/components/ui";
import { LocalizedText as Text, localizedAlert, useI18n } from "@/src/i18n";

export default function Abos() {
  useI18n();
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const subs = useQuery({ queryKey: ["subscriptions"], queryFn: () => apiGet("/subscriptions") });
  const companies = useQuery({ queryKey: ["companies"], queryFn: () => apiGet("/companies") });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });

  const activeProducts = (products.data ?? []).filter((p: any) => p.active !== false);
  const compMap: Record<string, any> = {};
  (companies.data ?? []).forEach((c: any) => (compMap[c.id] = c));
  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));

  const [companyId, setCompanyId] = useState("");
  const [productId, setProductId] = useState("");
  const [qty, setQty] = useState("20");
  const [interval, setInterval] = useState("28");
  const [showComp, setShowComp] = useState(false);
  const [showProd, setShowProd] = useState(false);
  const [msg, setMsg] = useState("");

  const create = useMutation({
    mutationFn: () => {
      return apiPostIdempotent("/subscriptions", {
        companyId,
        items: [{ productId, qty: Number(qty.replace(",", ".")) }],
        intervalDays: Number(interval.replace(",", ".")) || 28,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["subscriptions"] });
      setCompanyId("");
      setProductId("");
      setMsg("");
    },
    onError: (e: any) => setMsg(e.message || "Fehler"),
  });

  const toggle = useMutation({
    mutationFn: (sid: string) => apiPut(`/subscriptions/${sid}/toggle`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["subscriptions"] }),
  });
  const remove = async (sid: string) => {
    await apiDelete(`/subscriptions/${sid}`);
    qc.invalidateQueries({ queryKey: ["subscriptions"] });
  };
  const run = useMutation({
    mutationFn: () => apiPostIdempotent("/subscriptions/run", {}),
    onSuccess: (r: any) => {
      qc.invalidateQueries({ queryKey: ["orders"] });
      localizedAlert("Abos ausgeführt", `${r.count} fällige Bestellung(en) erstellt.`);
    },
  });

  const submit = () => {
    setMsg("");
    if (!companyId) return setMsg("Bitte Kunde wählen");
    if (!productId) return setMsg("Bitte Produkt wählen");
    create.mutate();
  };

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Abo-Bestellungen</Text>
          <Text style={styles.subtitle}>Wiederkehrende Lieferungen</Text>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Card testID="sub-form">
            <SectionTitle>Neues Abo</SectionTitle>
            <Text style={styles.label}>Kunde</Text>
            <Pressable style={styles.select} testID="sub-company" onPress={() => setShowComp((s) => !s)}>
              <Text style={styles.selectText}>{compMap[companyId]?.name ?? "Kunde wählen"}</Text>
              <CaretDown size={16} color={colors.muted} />
            </Pressable>
            {showComp &&
              (companies.data ?? []).map((c: any) => (
                <Pressable key={c.id} testID={`sub-company-${c.id}`} style={styles.option} onPress={() => { setCompanyId(c.id); setShowComp(false); }}>
                  <Text style={styles.optionText}>{c.name}</Text>
                </Pressable>
              ))}
            <Text style={styles.label}>Produkt</Text>
            <Pressable style={styles.select} testID="sub-product" onPress={() => setShowProd((s) => !s)}>
              <Text style={styles.selectText}>{prodMap[productId] ? `${prodMap[productId].brand} ${prodMap[productId].name}` : "Produkt wählen"}</Text>
              <CaretDown size={16} color={colors.muted} />
            </Pressable>
            {showProd &&
              activeProducts.map((p: any) => (
                <Pressable key={p.id} testID={`sub-product-${p.id}`} style={styles.option} onPress={() => { setProductId(p.id); setShowProd(false); }}>
                  <Text style={styles.optionText}>{p.brand} {p.name}</Text>
                </Pressable>
              ))}
            <View style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.label}>Menge</Text>
                <Input testID="sub-qty" value={qty} onChangeText={setQty} keyboardType="numeric" />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.label}>Intervall (Tage)</Text>
                <Input testID="sub-interval" value={interval} onChangeText={setInterval} keyboardType="numeric" />
              </View>
            </View>
            {msg ? <Text style={styles.err}>{msg}</Text> : null}
            <Button testID="sub-submit" title="Abo anlegen" loading={create.isPending} onPress={submit} style={{ marginTop: 10 }} />
          </Card>

          <Pressable testID="sub-run" style={styles.runBtn} onPress={() => run.mutate()}>
            <ArrowClockwise size={16} color={colors.brandPrimary} weight="bold" />
            <Text style={styles.runText}>{run.isPending ? "Wird ausgeführt…" : "Fällige Abos jetzt ausführen"}</Text>
          </Pressable>

          <SectionTitle style={{ marginTop: 4 }}>Aktive Abos ({subs.data?.length ?? 0})</SectionTitle>
          {(subs.data ?? []).length === 0 ? (
            <EmptyState title="Keine Abos" subtitle="Legen Sie oben ein wiederkehrendes Abo an" />
          ) : (
            (subs.data ?? []).map((s: any) => {
              const it = s.items?.[0];
              const p = it ? prodMap[it.productId] : null;
              return (
                <Card key={s.id} testID={`sub-${s.id}`} style={s.active === false ? { opacity: 0.55 } : undefined}>
                  <Text style={styles.subTitle}>{compMap[s.companyId]?.name ?? s.companyId}</Text>
                  <Muted>
                    {it?.productName || (p ? `${p.brand} ${p.name}` : it?.productId)} · {num(it?.qty ?? 0)} · alle {s.intervalDays} Tage
                  </Muted>
                  <Muted>Nächste Lieferung: {s.nextRun ? dateDE(s.nextRun) : "-"}</Muted>
                  <View style={styles.subActions}>
                    <Pressable testID={`sub-toggle-${s.id}`} style={styles.smallBtn} onPress={() => toggle.mutate(s.id)}>
                      <Text style={styles.smallBtnText}>{s.active === false ? "Aktivieren" : "Pausieren"}</Text>
                    </Pressable>
                    <Pressable testID={`sub-del-${s.id}`} style={[styles.smallBtn, styles.delBtn]} onPress={() => remove(s.id)}>
                      <Trash size={14} color={colors.onError} weight="bold" />
                      <Text style={[styles.smallBtnText, { color: colors.onError }]}>Löschen</Text>
                    </Pressable>
                  </View>
                </Card>
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
  header: { flexDirection: "row", alignItems: "flex-end", paddingHorizontal: 20, paddingBottom: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider, gap: 12 },
  backBtn: { width: 44, height: 44, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 24, fontWeight: "800", color: c.onSurface, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 2 },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  row: { flexDirection: "row", gap: 12 },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  err: { color: c.error, fontSize: 14, fontWeight: "600", marginTop: 8 },
  select: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: c.surfaceTertiary, borderRadius: 14, paddingHorizontal: 16, paddingVertical: 14 },
  selectText: { fontSize: 15, fontWeight: "600", color: c.onSurface },
  option: { backgroundColor: c.surfaceTertiary, borderRadius: 10, paddingHorizontal: 16, paddingVertical: 12, marginTop: 4 },
  optionText: { fontSize: 15, color: c.onSurfaceSecondary },
  runBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 12, borderRadius: 12, backgroundColor: c.brandTertiary },
  runText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  subTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  subActions: { flexDirection: "row", gap: 8, marginTop: 10 },
  smallBtn: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 14, paddingVertical: 9, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  smallBtnText: { fontSize: 13, fontWeight: "700", color: c.onSurface },
  delBtn: { backgroundColor: c.error },
}));
