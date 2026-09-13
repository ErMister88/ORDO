import { useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft, Plus, PencilSimple } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost, apiPut } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { Card, Input, Button, SectionTitle, Muted } from "@/src/components/ui";

type Form = {
  brand: string;
  name: string;
  unit: string;
  standardPrice: string;
  salesFloor: string;
  absoluteFloor: string;
  cost: string;
};

const EMPTY: Form = {
  brand: "",
  name: "",
  unit: "kg",
  standardPrice: "",
  salesFloor: "",
  absoluteFloor: "",
  cost: "",
};

export default function Produkte() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [showForm, setShowForm] = useState(false);
  const [msg, setMsg] = useState("");

  const set = (k: keyof Form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  const openNew = () => {
    setEditingId(null);
    setForm(EMPTY);
    setMsg("");
    setShowForm(true);
  };

  const openEdit = (p: any) => {
    setEditingId(p.id);
    setForm({
      brand: p.brand,
      name: p.name,
      unit: p.unit,
      standardPrice: String(p.standardPrice),
      salesFloor: String(p.salesFloor),
      absoluteFloor: String(p.absoluteFloor),
      cost: String(p.cost),
    });
    setMsg("");
    setShowForm(true);
  };

  const num = (v: string) => Number((v || "").replace(",", "."));

  const save = useMutation({
    mutationFn: () => {
      const body = {
        brand: form.brand,
        name: form.name,
        unit: form.unit,
        standardPrice: num(form.standardPrice),
        salesFloor: num(form.salesFloor),
        absoluteFloor: num(form.absoluteFloor),
        cost: num(form.cost),
        active: true,
      };
      return editingId ? apiPut(`/products/${editingId}`, body) : apiPost("/products", body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["products"] });
      setShowForm(false);
      setMsg("");
    },
    onError: (e: any) => setMsg(e.message),
  });

  const valid =
    form.brand && form.name && num(form.standardPrice) > 0 && num(form.cost) > 0 && num(form.absoluteFloor) > 0;

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Produkte</Text>
          <Text style={styles.subtitle}>Sortiment & Preise verwalten</Text>
        </View>
        <Pressable onPress={openNew} style={styles.addBtn} testID="add-product-button" hitSlop={8}>
          <Plus size={20} color={colors.onBrandPrimary} weight="bold" />
        </Pressable>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
          {showForm && (
            <Card testID="product-form">
              <SectionTitle>{editingId ? "Produkt bearbeiten" : "Neues Produkt"}</SectionTitle>
              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Marke</Text>
                  <Input testID="p-brand" value={form.brand} onChangeText={set("brand")} placeholder="z.B. Gambilongo" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Einheit</Text>
                  <Input testID="p-unit" value={form.unit} onChangeText={set("unit")} placeholder="kg" />
                </View>
              </View>
              <Text style={styles.label}>Bezeichnung</Text>
              <Input testID="p-name" value={form.name} onChangeText={set("name")} placeholder="z.B. Espresso Bar" />
              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Standardpreis €</Text>
                  <Input testID="p-standard" value={form.standardPrice} onChangeText={set("standardPrice")} keyboardType="decimal-pad" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Einkaufspreis €</Text>
                  <Input testID="p-cost" value={form.cost} onChangeText={set("cost")} keyboardType="decimal-pad" />
                </View>
              </View>
              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Vertriebslimit €</Text>
                  <Input testID="p-salesfloor" value={form.salesFloor} onChangeText={set("salesFloor")} keyboardType="decimal-pad" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Absolute Grenze €</Text>
                  <Input testID="p-absfloor" value={form.absoluteFloor} onChangeText={set("absoluteFloor")} keyboardType="decimal-pad" />
                </View>
              </View>
              {msg ? <Text style={styles.err}>{msg}</Text> : null}
              <View style={styles.row}>
                <Button title="Abbrechen" kind="secondary" style={{ flex: 1 }} onPress={() => setShowForm(false)} />
                <Button
                  testID="save-product"
                  title="Speichern"
                  style={{ flex: 1 }}
                  disabled={!valid}
                  loading={save.isPending}
                  onPress={() => save.mutate()}
                />
              </View>
            </Card>
          )}

          <SectionTitle style={{ marginTop: 4 }}>Sortiment ({products.data?.length ?? 0})</SectionTitle>
          {(products.data ?? []).map((p: any) => (
            <Pressable key={p.id} testID={`product-${p.id}`} onPress={() => openEdit(p)}>
              <Card>
                <View style={styles.prodTop}>
                  <Text style={styles.prodTitle}>
                    {p.brand} {p.name}
                  </Text>
                  <PencilSimple size={16} color={colors.muted} />
                </View>
                <Muted>
                  Standard {euro(p.standardPrice)} · Limit {euro(p.salesFloor)} · Grenze {euro(p.absoluteFloor)} · EK{" "}
                  {euro(p.cost)}
                </Muted>
              </Card>
            </Pressable>
          ))}
        </ScrollView>
      </KeyboardAvoidingView>
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
  addBtn: { width: 44, height: 44, borderRadius: 12, backgroundColor: c.brandPrimary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 24, fontWeight: "800", color: c.onSurface, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 2 },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  row: { flexDirection: "row", gap: 12 },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  err: { color: c.error, fontSize: 14, fontWeight: "600", marginTop: 6 },
  prodTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  prodTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface },
}));
