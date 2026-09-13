import { useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform, ActivityIndicator, Linking, TextInput } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Image } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import { ArrowLeft, Plus, Minus, PencilSimple, Camera, ImageSquare, X } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost, apiPut, apiUpload, fileUrl } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { Card, Input, Button, SectionTitle, Muted } from "@/src/components/ui";

type Tier = { minQty: string; price: string };

type Form = {
  brand: string;
  name: string;
  unit: string;
  standardPrice: string;
  salesFloor: string;
  absoluteFloor: string;
  cost: string;
  description: string;
  imageUrl: string;
  discountTiers: Tier[];
  taxRate: string;
  stock: string;
  b2cPrice: string;
};

const EMPTY: Form = {
  brand: "",
  name: "",
  unit: "kg",
  standardPrice: "",
  salesFloor: "",
  absoluteFloor: "",
  cost: "",
  description: "",
  imageUrl: "",
  discountTiers: [],
  taxRate: "7",
  stock: "",
  b2cPrice: "",
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
  const [ok, setOk] = useState("");
  const [uploading, setUploading] = useState(false);
  const [permBlocked, setPermBlocked] = useState(false);
  const [search, setSearch] = useState("");

  const toggleActive = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      apiPut(`/products/${id}/active`, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["products"] }),
  });

  const set = (k: keyof Form) => (v: string) => {
    setForm((f) => ({ ...f, [k]: v }));
    if (ok) setOk("");
  };

  const openNew = () => {
    setEditingId(null);
    setForm(EMPTY);
    setMsg("");
    setOk("");
    setPermBlocked(false);
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
      cost: String(p.cost ?? ""),
      description: p.description ?? "",
      imageUrl: p.imageUrl ?? "",
      discountTiers: (p.discountTiers ?? []).map((t: any) => ({
        minQty: String(t.minQty),
        price: String(t.price),
      })),
      taxRate: String(p.taxRate ?? 7),
      stock: p.stock != null ? String(p.stock) : "",
      b2cPrice: p.b2cPrice != null ? String(p.b2cPrice) : "",
    });
    setMsg("");
    setOk("");
    setPermBlocked(false);
    setShowForm(true);
  };

  const pickImage = async () => {
    setPermBlocked(false);
    let perm = await ImagePicker.getMediaLibraryPermissionsAsync();
    if (perm.status === "undetermined" || (perm.status === "denied" && perm.canAskAgain)) {
      perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    }
    if (perm.status !== "granted") {
      setPermBlocked(true);
      return;
    }
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.7,
      allowsEditing: true,
      aspect: [4, 3],
    });
    if (res.canceled || !res.assets?.length) return;
    const asset = res.assets[0];
    setUploading(true);
    setMsg("");
    try {
      const name = asset.fileName || `produkt.${(asset.uri.split(".").pop() || "jpg").split("?")[0]}`;
      const type = asset.mimeType || "image/jpeg";
      const up = await apiUpload(asset.uri, name, type);
      setForm((f) => ({ ...f, imageUrl: up.url }));
      if (ok) setOk("");
    } catch (e: any) {
      setMsg(e.message || "Upload fehlgeschlagen");
    } finally {
      setUploading(false);
    }
  };

  const num = (v: string) => Number((v || "").replace(",", "."));

  const setTier = (idx: number, key: keyof Tier) => (v: string) => {
    setForm((f) => ({
      ...f,
      discountTiers: f.discountTiers.map((t, i) => (i === idx ? { ...t, [key]: v } : t)),
    }));
    if (ok) setOk("");
  };
  const addTier = () =>
    setForm((f) => ({ ...f, discountTiers: [...f.discountTiers, { minQty: "", price: "" }] }));
  const removeTier = (idx: number) =>
    setForm((f) => ({ ...f, discountTiers: f.discountTiers.filter((_, i) => i !== idx) }));

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
        description: form.description,
        imageUrl: form.imageUrl,
        discountTiers: form.discountTiers
          .map((t) => ({ minQty: num(t.minQty), price: num(t.price) }))
          .filter((t) => t.minQty > 0 && t.price > 0)
          .sort((a, b) => a.minQty - b.minQty),
        taxRate: Number(form.taxRate) === 19 ? 19 : 7,
        stock: form.stock.trim() === "" ? null : num(form.stock),
        b2cPrice: form.b2cPrice.trim() === "" ? null : num(form.b2cPrice),
        active: true,
      };
      return editingId ? apiPut(`/products/${editingId}`, body) : apiPost("/products", body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["products"] });
      setMsg("");
      if (editingId) {
        setShowForm(false);
      } else {
        setOk(`„${form.brand} ${form.name}" hinzugefügt. Nächstes Produkt eingeben.`);
        setForm(EMPTY);
      }
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

              <Text style={styles.label}>Produktbild</Text>
              {form.imageUrl ? (
                <View style={styles.imgWrap}>
                  <Image source={{ uri: fileUrl(form.imageUrl) }} style={styles.img} contentFit="cover" transition={200} />
                  <Pressable
                    testID="remove-image"
                    style={styles.imgRemove}
                    onPress={() => setForm((f) => ({ ...f, imageUrl: "" }))}
                    hitSlop={8}
                  >
                    <X size={16} color={colors.onError} weight="bold" />
                  </Pressable>
                </View>
              ) : (
                <Pressable testID="pick-image" style={styles.imgPicker} onPress={pickImage} disabled={uploading}>
                  {uploading ? (
                    <ActivityIndicator color={colors.brandPrimary} />
                  ) : (
                    <>
                      <ImageSquare size={26} color={colors.brandPrimary} weight="duotone" />
                      <Text style={styles.imgPickerText}>Bild auswählen</Text>
                    </>
                  )}
                </Pressable>
              )}
              {form.imageUrl && !uploading ? (
                <Pressable testID="change-image" style={styles.changeImg} onPress={pickImage}>
                  <Camera size={16} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.changeImgText}>Bild ändern</Text>
                </Pressable>
              ) : null}
              {permBlocked ? (
                <View style={{ marginTop: 6, gap: 6 }}>
                  <Text style={styles.err}>Zugriff auf Fotos wurde abgelehnt.</Text>
                  <Button title="Einstellungen öffnen" kind="secondary" testID="open-settings" onPress={() => Linking.openSettings()} />
                </View>
              ) : null}

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

              <Text style={styles.label}>Beschreibung</Text>
              <Input
                testID="p-description"
                value={form.description}
                onChangeText={set("description")}
                placeholder="Herkunft, Röstung, Geschmack …"
                multiline
                numberOfLines={4}
                style={styles.textArea}
              />

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

              <Text style={styles.label}>MwSt-Satz</Text>
              <View style={styles.row}>
                {[7, 19].map((r) => (
                  <Pressable
                    key={r}
                    testID={`tax-${r}`}
                    style={[styles.taxBtn, Number(form.taxRate) === r && styles.taxBtnActive]}
                    onPress={() => setForm((f) => ({ ...f, taxRate: String(r) }))}
                  >
                    <Text style={[styles.taxText, Number(form.taxRate) === r && styles.taxTextActive]}>
                      {r}% {r === 7 ? "(Kaffee)" : "(Maschine)"}
                    </Text>
                  </Pressable>
                ))}
              </View>

              <Text style={styles.label}>Lagerbestand (optional, leer = unbegrenzt)</Text>
              <Input testID="p-stock" value={form.stock} onChangeText={set("stock")} keyboardType="numeric" placeholder="z. B. 120" />

              <Text style={styles.label}>B2C-Shop-Preis € (brutto, leer = nicht im Shop)</Text>
              <Input testID="p-b2cprice" value={form.b2cPrice} onChangeText={set("b2cPrice")} keyboardType="decimal-pad" placeholder="z. B. 19,90" />

              <Text style={styles.label}>Mengenrabatt-Staffeln (optional)</Text>
              <Muted>Ab welcher Menge gilt welcher Stückpreis? Greift automatisch in Angeboten & Nachbestellungen.</Muted>
              {form.discountTiers.map((t, idx) => (
                <View key={idx} style={styles.tierRow} testID={`tier-row-${idx}`}>
                  <View style={{ flex: 1 }}>
                    <Input
                      testID={`tier-qty-${idx}`}
                      value={t.minQty}
                      onChangeText={setTier(idx, "minQty")}
                      keyboardType="numeric"
                      placeholder={`ab Menge (${form.unit})`}
                    />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Input
                      testID={`tier-price-${idx}`}
                      value={t.price}
                      onChangeText={setTier(idx, "price")}
                      keyboardType="decimal-pad"
                      placeholder="Preis €"
                    />
                  </View>
                  <Pressable testID={`tier-remove-${idx}`} onPress={() => removeTier(idx)} style={styles.tierRemove} hitSlop={8}>
                    <X size={16} color={colors.onError} weight="bold" />
                  </Pressable>
                </View>
              ))}
              <Pressable testID="add-tier" style={styles.addTierBtn} onPress={addTier}>
                <Plus size={16} color={colors.brandPrimary} weight="bold" />
                <Text style={styles.addTierText}>Staffel hinzufügen</Text>
              </Pressable>

              {msg ? <Text style={styles.err}>{msg}</Text> : null}
              {ok ? <Text testID="product-saved-msg" style={styles.ok}>{ok}</Text> : null}
              <View style={styles.row}>
                <Button title={editingId ? "Abbrechen" : "Fertig"} kind="secondary" style={{ flex: 1 }} onPress={() => setShowForm(false)} />
                <Button
                  testID="save-product"
                  title={editingId ? "Speichern" : "Hinzufügen"}
                  style={{ flex: 1 }}
                  disabled={!valid || uploading}
                  loading={save.isPending}
                  onPress={() => save.mutate()}
                />
              </View>
            </Card>
          )}

          <SectionTitle style={{ marginTop: 4 }}>Sortiment ({products.data?.length ?? 0})</SectionTitle>
          <Input
            testID="product-search"
            value={search}
            onChangeText={setSearch}
            placeholder="Produkt suchen (Marke, Name, Beschreibung)…"
            style={{ marginBottom: 4 }}
          />
          {(() => {
            const q = search.trim().toLowerCase();
            const list = (products.data ?? []).filter(
              (p: any) =>
                !q ||
                `${p.brand} ${p.name}`.toLowerCase().includes(q) ||
                (p.description ?? "").toLowerCase().includes(q),
            );
            if (list.length === 0) {
              return <Muted testID="no-products">Keine Produkte gefunden.</Muted>;
            }
            return list.map((p: any) => {
              const inactive = p.active === false;
              return (
                <Pressable key={p.id} testID={`product-${p.id}`} onPress={() => openEdit(p)}>
                  <Card style={inactive ? { opacity: 0.55 } : undefined}>
                    <View style={styles.prodRow}>
                      {p.imageUrl ? (
                        <Image source={{ uri: fileUrl(p.imageUrl) }} style={styles.thumb} contentFit="cover" transition={150} />
                      ) : (
                        <View style={[styles.thumb, styles.thumbEmpty]}>
                          <ImageSquare size={22} color={colors.muted} weight="duotone" />
                        </View>
                      )}
                      <View style={{ flex: 1 }}>
                        <View style={styles.prodTop}>
                          <Text style={styles.prodTitle} numberOfLines={1}>
                            {p.brand} {p.name}
                          </Text>
                          <PencilSimple size={16} color={colors.muted} />
                        </View>
                        {p.description ? <Muted numberOfLines={2}>{p.description}</Muted> : null}
                        <Muted>
                          Standard {euro(p.standardPrice)} · Limit {euro(p.salesFloor)} · Grenze {euro(p.absoluteFloor)} · EK{" "}
                          {euro(p.cost)}
                        </Muted>
                        {p.discountTiers?.length ? (
                          <Muted>
                            Staffel: {p.discountTiers.map((t: any) => `ab ${t.minQty} → ${euro(t.price)}`).join(" · ")}
                          </Muted>
                        ) : null}
                        <Muted>
                          MwSt {p.taxRate ?? 7}%
                          {p.stock != null ? ` · Lager: ${p.stock}${p.stock <= 0 ? " (leer)" : ""}` : ""}
                        </Muted>
                        <QuickStock product={p} />
                      </View>
                    </View>
                    <Pressable
                      testID={`toggle-active-${p.id}`}
                      style={[styles.activePill, inactive ? styles.pillOff : styles.pillOn]}
                      onPress={() => toggleActive.mutate({ id: p.id, active: inactive })}
                      hitSlop={6}
                    >
                      <Text style={[styles.pillText, { color: inactive ? colors.muted : colors.success }]}>
                        {inactive ? "Inaktiv · antippen zum Aktivieren" : "Aktiv · antippen zum Ausblenden"}
                      </Text>
                    </Pressable>
                  </Card>
                </Pressable>
              );
            });
          })()}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function QuickStock({ product }: { product: any }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const qc = useQueryClient();
  const [val, setVal] = useState(product.stock != null ? String(product.stock) : "");
  const save = useMutation({
    mutationFn: (stock: number | null) => apiPut(`/products/${product.id}/stock`, { stock }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["products"] }),
  });
  const cur = val.trim() === "" ? null : Math.max(0, Number(val.replace(",", ".")) || 0);
  return (
    <View style={styles.quickStock}>
      <Text style={styles.quickLabel}>Bestand</Text>
      <Pressable testID={`stock-minus-${product.id}`} style={styles.stockStep} onPress={() => setVal(String(Math.max(0, (cur ?? 0) - 1)))} hitSlop={6}>
        <Minus size={15} color={colors.onSurface} weight="bold" />
      </Pressable>
      <TextInput
        testID={`stock-input-${product.id}`}
        style={styles.stockInput}
        value={val}
        onChangeText={setVal}
        keyboardType="numeric"
        placeholder="∞"
        placeholderTextColor={colors.muted}
      />
      <Pressable testID={`stock-plus-${product.id}`} style={styles.stockStep} onPress={() => setVal(String((cur ?? 0) + 1))} hitSlop={6}>
        <Plus size={15} color={colors.onSurface} weight="bold" />
      </Pressable>
      <Pressable testID={`stock-save-${product.id}`} style={styles.stockSave} onPress={() => save.mutate(cur)} hitSlop={6}>
        <Text style={styles.stockSaveTxt}>{save.isPending ? "…" : "Speichern"}</Text>
      </Pressable>
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
  textArea: { height: 92, textAlignVertical: "top", paddingTop: 12 },
  err: { color: c.error, fontSize: 14, fontWeight: "600", marginTop: 6 },
  ok: { color: c.success, fontSize: 14, fontWeight: "700", marginTop: 6 },
  prodRow: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  prodTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  prodTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface, flex: 1 },
  thumb: { width: 56, height: 56, borderRadius: 12, backgroundColor: c.surfaceTertiary },
  thumbEmpty: { alignItems: "center", justifyContent: "center" },
  imgWrap: { position: "relative", borderRadius: 14, overflow: "hidden" },
  img: { width: "100%", height: 170, borderRadius: 14, backgroundColor: c.surfaceTertiary },
  imgRemove: {
    position: "absolute",
    top: 8,
    right: 8,
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: c.error,
    alignItems: "center",
    justifyContent: "center",
  },
  imgPicker: {
    height: 120,
    borderRadius: 14,
    borderWidth: 1.5,
    borderStyle: "dashed",
    borderColor: c.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: c.brandTertiary,
  },
  imgPickerText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  changeImg: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 8 },
  changeImgText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  tierRow: { flexDirection: "row", gap: 8, alignItems: "center", marginTop: 8 },
  tierRemove: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: c.error,
    alignItems: "center",
    justifyContent: "center",
  },
  addTierBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: 10,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: c.brandPrimary,
    borderStyle: "dashed",
  },
  addTierText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  activePill: {
    marginTop: 10,
    paddingVertical: 8,
    borderRadius: 10,
    alignItems: "center",
    backgroundColor: c.surfaceTertiary,
  },
  pillOn: { backgroundColor: c.brandTertiary },
  pillOff: { backgroundColor: c.surfaceTertiary },
  pillText: { fontSize: 13, fontWeight: "700" },
  taxBtn: { flex: 1, paddingVertical: 12, borderRadius: 12, alignItems: "center", backgroundColor: c.surfaceTertiary },
  taxBtnActive: { backgroundColor: c.brandPrimary },
  taxText: { fontSize: 14, fontWeight: "700", color: c.onSurfaceSecondary },
  taxTextActive: { color: c.onBrandPrimary },
  quickStock: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 10 },
  quickLabel: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginRight: 2 },
  stockStep: { width: 32, height: 32, borderRadius: 8, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  stockInput: { width: 56, height: 36, borderRadius: 8, backgroundColor: c.surfaceTertiary, textAlign: "center", fontSize: 15, fontWeight: "700", color: c.onSurface, paddingVertical: 0 },
  stockSave: { marginLeft: "auto", paddingHorizontal: 12, height: 32, borderRadius: 8, backgroundColor: c.brandTertiary, alignItems: "center", justifyContent: "center" },
  stockSaveTxt: { fontSize: 13, fontWeight: "700", color: c.brandPrimary },
}));
