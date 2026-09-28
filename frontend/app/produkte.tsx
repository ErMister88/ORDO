import { useDeferredValue, useState } from "react";
import {
  View,
  ScrollView,
  Pressable,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Linking,
  TextInput,
} from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Image } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import { ArrowLeft, Plus, Minus, PencilSimple, Camera, ImageSquare, X } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiDelete, apiGet, apiPost, apiPut, apiUpload, fileUrl } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { Card, Input, Button, SectionTitle, Muted } from "@/src/components/ui";
import { uploadsEnabled } from "@/src/config/features";
import { LocalizedText as Text, localizedAlert, useI18n } from "@/src/i18n";

type Tier = { minQty: string; price: string };
type VariantForm = { id: string; name: string; sku: string; ean: string; weight: string; unit: string; imageUrls: string; availability: "available" | "unavailable" | "preorder"; active: boolean };

type Form = {
  sku: string;
  ean: string;
  brand: string;
  brandId: string;
  name: string;
  categoryId: string;
  collectionIds: string[];
  unit: string;
  packagingUnit: string;
  packageQuantity: string;
  unitsPerCase: string;
  kgPerCase: string;
  casesPerPallet: string;
  kgPerPallet: string;
  contentAmount: string;
  contentUnit: string;
  minimumOrderQuantity: string;
  b2bAvailable: boolean;
  b2cAvailable: boolean;
  directPurchaseAllowed: boolean;
  financingRequestAllowed: boolean;
  standardPrice: string;
  salesFloor: string;
  absoluteFloor: string;
  cost: string;
  description: string;
  imageUrl: string;
  discountTiers: Tier[];
  b2cTiers: Tier[];
  taxRate: string;
  stock: string;
  b2cPrice: string;
  slug: string;
  searchKeywords: string;
  availability: "available" | "unavailable" | "preorder";
  quickAdd: boolean;
  subscriptionAllowed: boolean;
  subscriptionIntervals: string;
  variants: VariantForm[];
  regionIds: string[];
  shippingClassId: string;
  attributeValues: Record<string, string | number | boolean | string[]>;
  relatedProductIds: string[];
  recommendedProductIds: string[];
  compatibleProductIds: string[];
  foodInfo: Record<string, unknown>;
  seoTitle: string;
  seoDescription: string;
};

const EMPTY: Form = {
  sku: "",
  ean: "",
  brand: "",
  brandId: "",
  name: "",
  categoryId: "",
  collectionIds: [],
  unit: "piece",
  packagingUnit: "",
  packageQuantity: "",
  unitsPerCase: "",
  kgPerCase: "",
  casesPerPallet: "",
  kgPerPallet: "",
  contentAmount: "",
  contentUnit: "",
  minimumOrderQuantity: "",
  b2bAvailable: true,
  b2cAvailable: false,
  directPurchaseAllowed: true,
  financingRequestAllowed: false,
  standardPrice: "",
  salesFloor: "",
  absoluteFloor: "",
  cost: "",
  description: "",
  imageUrl: "",
  discountTiers: [],
  b2cTiers: [],
  taxRate: "7",
  stock: "",
  b2cPrice: "",
  slug: "", searchKeywords: "", availability: "available", quickAdd: true,
  subscriptionAllowed: true, subscriptionIntervals: "monthly", variants: [],
  regionIds: [], shippingClassId: "", attributeValues: {}, relatedProductIds: [],
  recommendedProductIds: [], compatibleProductIds: [], foodInfo: {}, seoTitle: "", seoDescription: "",
};

function managedFileId(value: string): string | null {
  const match = value.match(/(?:^|\/api\/files\/)(file_[0-9a-f]{24})(?:$|[?#])/);
  return match?.[1] ?? null;
}

export default function Produkte() {
  const { tf } = useI18n();
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const products = useQuery({ queryKey: ["products", deferredSearch], queryFn: () => apiGet(`/products?limit=500&q=${encodeURIComponent(deferredSearch)}`) });
  const categories = useQuery({ queryKey: ["product-categories"], queryFn: () => apiGet("/product-categories") });
  const collections = useQuery({ queryKey: ["shop-collections-admin"], queryFn: () => apiGet("/shop-collections") });
  const brands = useQuery({ queryKey: ["config-brands"], queryFn: () => apiGet("/business-config/brands") });
  const attributes = useQuery({ queryKey: ["commerce-attributes"], queryFn: () => apiGet("/commerce/attributes") });
  const regions = useQuery({ queryKey: ["commerce-admin", "regions"], queryFn: () => apiGet("/commerce/entities/regions") });
  const shippingClasses = useQuery({ queryKey: ["commerce-admin", "shipping-classes"], queryFn: () => apiGet("/commerce/entities/shipping-classes") });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [showForm, setShowForm] = useState(false);
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState("");
  const [uploading, setUploading] = useState(false);
  const [originalImageUrl, setOriginalImageUrl] = useState("");
  const [imagePreviewUrl, setImagePreviewUrl] = useState("");
  const [permBlocked, setPermBlocked] = useState(false);
  const [showCategories, setShowCategories] = useState(false);
  const [showBrands, setShowBrands] = useState(false);
  const [newCategory, setNewCategory] = useState("");
  const productImages = useQuery({ queryKey: ["product-images", editingId], queryFn: () => apiGet(`/products/${editingId}/images`), enabled: Boolean(editingId && uploadsEnabled) });
  const createCategory = useMutation({ mutationFn: () => apiPost("/product-categories", { name: newCategory }), onSuccess: (category: any) => { qc.invalidateQueries({ queryKey: ["product-categories"] }); setForm((value) => ({ ...value, categoryId: category.id })); setNewCategory(""); } });

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
    setOriginalImageUrl("");
    setImagePreviewUrl("");
    setMsg("");
    setOk("");
    setPermBlocked(false);
    setShowForm(true);
  };

  const openEdit = (p: any) => {
    setEditingId(p.id);
    setOriginalImageUrl(p.imageUrl ?? "");
    setImagePreviewUrl("");
    setForm({
      sku: p.sku ?? "",
      ean: p.ean ?? "",
      brand: p.brand ?? "",
      brandId: p.brandId ?? "",
      name: p.name,
      categoryId: p.categoryId ?? "",
      collectionIds: p.collectionIds ?? [],
      unit: p.unit,
      packagingUnit: p.packagingUnit ?? "",
      packageQuantity: p.packageQuantity != null ? String(p.packageQuantity) : "",
      unitsPerCase: p.unitsPerCase != null ? String(p.unitsPerCase) : "",
      kgPerCase: p.kgPerCase != null ? String(p.kgPerCase) : "",
      casesPerPallet: p.casesPerPallet != null ? String(p.casesPerPallet) : "",
      kgPerPallet: p.kgPerPallet != null ? String(p.kgPerPallet) : "",
      contentAmount: p.contentAmount != null ? String(p.contentAmount) : "",
      contentUnit: p.contentUnit ?? "",
      minimumOrderQuantity: p.minimumOrderQuantity != null ? String(p.minimumOrderQuantity) : "",
      b2bAvailable: p.b2bAvailable !== false,
      b2cAvailable: p.b2cAvailable !== false && p.b2cPrice != null,
      directPurchaseAllowed: p.directPurchaseAllowed !== false,
      financingRequestAllowed: p.financingRequestAllowed === true,
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
      b2cTiers: (p.b2cTiers ?? []).map((t: any) => ({ minQty: String(t.minQty), price: String(t.price) })),
      taxRate: String(p.taxRate ?? 7),
      stock: p.stock != null ? String(p.stock) : "",
      b2cPrice: p.b2cPrice != null ? String(p.b2cPrice) : "",
      slug: p.slug ?? "",
      searchKeywords: (p.searchKeywords ?? []).join(", "),
      availability: p.availability ?? "available",
      quickAdd: p.quickAdd !== false,
      subscriptionAllowed: p.subscriptionAllowed !== false,
      subscriptionIntervals: (p.subscriptionIntervals ?? []).join(", ") || "monthly",
      variants: (p.variants ?? []).map((variant: any) => ({ id: variant.id, name: variant.name ?? "", sku: variant.sku ?? "", ean: variant.ean ?? "", weight: variant.weight != null ? String(variant.weight) : "", unit: variant.unit ?? "", imageUrls: (variant.imageUrls ?? []).join(", "), availability: variant.availability ?? "available", active: variant.active !== false })),
      regionIds: p.regionIds ?? [], shippingClassId: p.shippingClassId ?? "",
      attributeValues: p.attributeValues ?? {}, relatedProductIds: p.relatedProductIds ?? [], recommendedProductIds: p.recommendedProductIds ?? [], compatibleProductIds: p.compatibleProductIds ?? [],
      foodInfo: p.foodInfo ?? {}, seoTitle: p.seoTitle ?? "", seoDescription: p.seoDescription ?? "",
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
      const up = await apiUpload(asset.uri, name, type, {
        resourceType: "product", resourceId: editingId || "unassigned", visibility: "public",
      });
      setForm((f) => ({ ...f, imageUrl: up.url }));
      setImagePreviewUrl(asset.uri);
      if (ok) setOk("");
    } catch (e: any) {
      setMsg(e.message || "Upload fehlgeschlagen");
    } finally {
      setUploading(false);
    }
  };

  const pickGalleryImages = async () => {
    if (!editingId) return;
    setPermBlocked(false);
    let perm = await ImagePicker.getMediaLibraryPermissionsAsync();
    if (perm.status === "undetermined" || (perm.status === "denied" && perm.canAskAgain)) perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (perm.status !== "granted") { setPermBlocked(true); return; }
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.75, allowsMultipleSelection: true, selectionLimit: 10 });
    if (res.canceled || !res.assets?.length) return;
    setUploading(true); setMsg("");
    try {
      const start = productImages.data?.length ?? 0;
      let newPrimaryUrl = "";
      for (const [index, asset] of res.assets.entries()) {
        const name = asset.fileName || `produkt-${index + 1}.${(asset.uri.split(".").pop() || "jpg").split("?")[0]}`;
        const uploaded = await apiUpload(asset.uri, name, asset.mimeType || "image/jpeg", { resourceType: "product", resourceId: editingId, visibility: "public" });
        const fileId = managedFileId(uploaded.url);
        if (!fileId) throw new Error("Die Bildreferenz konnte nicht sicher zugeordnet werden.");
        await apiPut(`/products/${editingId}/images/${fileId}`, { sortOrder: start + index, isPrimary: start === 0 && index === 0 });
        if (start === 0 && index === 0) newPrimaryUrl = uploaded.url;
      }
      if (newPrimaryUrl) { setForm((value) => ({ ...value, imageUrl: newPrimaryUrl })); setOriginalImageUrl(newPrimaryUrl); }
      await productImages.refetch();
      qc.invalidateQueries({ queryKey: ["products"] });
    } catch (error) {
      setMsg(error instanceof Error ? error.message : "Galerie-Upload fehlgeschlagen");
    } finally { setUploading(false); }
  };

  const setPrimaryImage = async (image: any) => {
    if (!editingId) return;
    try {
      await apiPut(`/products/${editingId}/images/${image.id}`, { sortOrder: image.sortOrder ?? 0, isPrimary: true });
      setForm((value) => ({ ...value, imageUrl: image.url }));
      setOriginalImageUrl(image.url);
      await productImages.refetch();
      qc.invalidateQueries({ queryKey: ["products"] });
    } catch (error) { localizedAlert("Bild konnte nicht aktualisiert werden", error instanceof Error ? error.message : "Bitte erneut versuchen."); }
  };

  const removeGalleryImage = async (image: any) => {
    if (image.isPrimary && (productImages.data?.length ?? 0) <= 1) {
      localizedAlert("Hauptbild beibehalten", "Ersetze das Hauptbild zuerst oder entferne es über die Produktmaske und speichere das Produkt.");
      return;
    }
    try {
      if (image.isPrimary) {
        const replacement = productImages.data.find((item: any) => item.id !== image.id);
        await apiPut(`/products/${editingId}/images/${replacement.id}`, { sortOrder: replacement.sortOrder ?? 0, isPrimary: true });
        setForm((value) => ({ ...value, imageUrl: replacement.url }));
        setOriginalImageUrl(replacement.url);
      }
      await apiDelete(`/files/${image.id}`);
      await productImages.refetch();
      qc.invalidateQueries({ queryKey: ["products"] });
    } catch (error) { localizedAlert("Bild konnte nicht entfernt werden", error instanceof Error ? error.message : "Bitte erneut versuchen."); }
  };

  const moveGalleryImage = async (image: any, direction: -1 | 1) => {
    if (!editingId) return;
    const ordered = [...(productImages.data ?? [])].sort((left: any, right: any) => (left.sortOrder ?? 0) - (right.sortOrder ?? 0));
    const index = ordered.findIndex((item: any) => item.id === image.id);
    const other = ordered[index + direction];
    if (!other) return;
    try {
      await Promise.all([
        apiPut(`/products/${editingId}/images/${image.id}`, { sortOrder: other.sortOrder ?? index + direction, isPrimary: Boolean(image.isPrimary) }),
        apiPut(`/products/${editingId}/images/${other.id}`, { sortOrder: image.sortOrder ?? index, isPrimary: Boolean(other.isPrimary) }),
      ]);
      await productImages.refetch();
    } catch (error) { localizedAlert("Reihenfolge konnte nicht geändert werden", error instanceof Error ? error.message : "Bitte erneut versuchen."); }
  };

  const num = (v: string) => Number((v || "").replace(",", "."));

  const setTier = (idx: number, key: keyof Tier) => (v: string) => {
    setForm((f) => ({
      ...f,
      b2cTiers: f.b2cTiers.map((t, i) => (i === idx ? { ...t, [key]: v } : t)),
    }));
    if (ok) setOk("");
  };
  const addTier = () =>
    setForm((f) => ({ ...f, b2cTiers: [...f.b2cTiers, { minQty: "", price: "" }] }));
  const removeTier = (idx: number) =>
    setForm((f) => ({ ...f, b2cTiers: f.b2cTiers.filter((_, i) => i !== idx) }));

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        sku: form.sku,
        ean: form.ean,
        brand: form.brand,
        brandId: form.brandId || null,
        name: form.name,
        categoryId: form.categoryId || null,
        collectionIds: form.collectionIds,
        unit: form.unit,
        packagingUnit: form.packagingUnit,
        packageQuantity: form.packageQuantity ? num(form.packageQuantity) : null,
        unitsPerCase: form.unitsPerCase ? num(form.unitsPerCase) : null,
        kgPerCase: form.kgPerCase ? num(form.kgPerCase) : null,
        casesPerPallet: form.casesPerPallet ? num(form.casesPerPallet) : null,
        kgPerPallet: form.kgPerPallet ? num(form.kgPerPallet) : null,
        contentAmount: form.contentAmount ? num(form.contentAmount) : null,
        contentUnit: form.contentUnit,
        minimumOrderQuantity: form.minimumOrderQuantity ? num(form.minimumOrderQuantity) : null,
        b2bAvailable: form.b2bAvailable,
        b2cAvailable: form.b2cAvailable,
        directPurchaseAllowed: form.directPurchaseAllowed,
        financingRequestAllowed: form.financingRequestAllowed,
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
        b2cTiers: form.b2cTiers
          .map((t) => ({ minQty: num(t.minQty), price: num(t.price) }))
          .filter((t) => t.minQty > 0 && t.price > 0)
          .sort((a, b) => a.minQty - b.minQty),
        taxRate: Math.round(num(form.taxRate)),
        stock: form.stock.trim() === "" ? null : num(form.stock),
        b2cPrice: form.b2cPrice.trim() === "" ? null : num(form.b2cPrice),
        active: true,
        slug: form.slug,
        searchKeywords: form.searchKeywords.split(",").map((value) => value.trim()).filter(Boolean),
        availability: form.availability,
        quickAdd: form.quickAdd,
        subscriptionAllowed: form.subscriptionAllowed,
        subscriptionIntervals: form.subscriptionAllowed ? form.subscriptionIntervals.split(",").map((value) => value.trim()).filter(Boolean) : [],
        variants: form.variants.map((variant) => ({ ...variant, weight: variant.weight ? num(variant.weight) : null, imageUrls: variant.imageUrls.split(",").map((value) => value.trim()).filter(Boolean) })),
        regionIds: form.regionIds,
        shippingClassId: form.shippingClassId || null,
        attributeValues: form.attributeValues,
        relatedProductIds: form.relatedProductIds,
        recommendedProductIds: form.recommendedProductIds,
        compatibleProductIds: form.compatibleProductIds,
        foodInfo: form.foodInfo,
        seoTitle: form.seoTitle,
        seoDescription: form.seoDescription,
      };
      const product: any = editingId
        ? await apiPut(`/products/${editingId}`, body)
        : await apiPost("/products", body);
      const productId = product.id ?? editingId;
      const nextFileId = managedFileId(form.imageUrl);
      try {
        if (productId && nextFileId) {
          await apiPut(`/products/${productId}/images/${nextFileId}`, { sortOrder: 0, isPrimary: true });
        }
        const previousFileId = managedFileId(originalImageUrl);
        if (previousFileId && previousFileId !== nextFileId) {
          await apiDelete(`/files/${previousFileId}`);
        }
      } catch {
        product._mediaWarning = "Das Produkt wurde gespeichert, aber die Bildzuordnung konnte nicht vollständig abgeschlossen werden.";
      }
      return product;
    },
    onSuccess: (product: any) => {
      qc.invalidateQueries({ queryKey: ["products"] });
      if (product?.id) qc.invalidateQueries({ queryKey: ["product-images", product.id] });
      if (product?._mediaWarning) localizedAlert("Bildzuordnung prüfen", product._mediaWarning);
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
    form.name && form.unit && num(form.standardPrice) > 0 && num(form.cost) >= 0 && num(form.absoluteFloor) > 0;
  const categoryLineage = new Set<string>();
  let categoryCursor = form.categoryId;
  while (categoryCursor && !categoryLineage.has(categoryCursor)) {
    categoryLineage.add(categoryCursor);
    categoryCursor = (categories.data ?? []).find((category: any) => category.id === categoryCursor)?.parentId ?? "";
  }
  const relevantAttributes = (attributes.data ?? []).filter((attribute: any) => attribute.active !== false && (
    !attribute.categoryIds?.length || attribute.categoryIds.some((categoryId: string) => categoryLineage.has(categoryId))
  ));

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

              {uploadsEnabled || form.imageUrl ? <Text style={styles.label}>Produktbild</Text> : null}
              {form.imageUrl ? (
                <View style={styles.imgWrap}>
                  <Image source={{ uri: imagePreviewUrl || fileUrl(form.imageUrl) }} style={styles.img} contentFit="cover" transition={200} />
                  {uploadsEnabled ? (
                    <Pressable
                      testID="remove-image"
                      style={styles.imgRemove}
                      onPress={() => { setForm((f) => ({ ...f, imageUrl: "" })); setImagePreviewUrl(""); }}
                      hitSlop={8}
                    >
                      <X size={16} color={colors.onError} weight="bold" />
                    </Pressable>
                  ) : null}
                </View>
              ) : uploadsEnabled ? (
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
              ) : null}
              {uploadsEnabled && form.imageUrl && !uploading ? (
                <Pressable testID="change-image" style={styles.changeImg} onPress={pickImage}>
                  <Camera size={16} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.changeImgText}>Bild ändern</Text>
                </Pressable>
              ) : null}
              {uploadsEnabled && editingId ? <View style={styles.galleryAdmin}><View style={styles.galleryHeader}><Text style={styles.label}>Produktgalerie</Text><Button title="Bilder hinzufügen" kind="secondary" loading={uploading} onPress={pickGalleryImages} /></View><View style={styles.galleryGrid}>{(productImages.data ?? []).map((image: any, index: number, all: any[]) => <View key={image.id} style={styles.galleryItem}><Image source={{ uri: fileUrl(image.url) }} style={styles.galleryImage} contentFit="cover" /><Text style={styles.galleryBadge}>{image.isPrimary ? "Hauptbild" : tf("Position {position}", { position: (image.sortOrder ?? 0) + 1 })}</Text><View style={styles.actions}><Button title="Nach oben" kind="secondary" disabled={index === 0} onPress={() => { void moveGalleryImage(image, -1); }} /><Button title="Nach unten" kind="secondary" disabled={index === all.length - 1} onPress={() => { void moveGalleryImage(image, 1); }} />{!image.isPrimary ? <Button title="Als Hauptbild" kind="secondary" onPress={() => { void setPrimaryImage(image); }} /> : null}<Button title="Entfernen" kind="secondary" onPress={() => { void removeGalleryImage(image); }} /></View></View>)}</View></View> : null}
              {permBlocked ? (
                <View style={{ marginTop: 6, gap: 6 }}>
                  <Text style={styles.err}>Zugriff auf Fotos wurde abgelehnt.</Text>
                  <Button title="Einstellungen öffnen" kind="secondary" testID="open-settings" onPress={() => Linking.openSettings()} />
                </View>
              ) : null}

              <View style={styles.row}>
                <View style={{ flex: 1 }}><Text style={styles.label}>Artikelnummer / SKU</Text><Input testID="p-sku" value={form.sku} onChangeText={set("sku")} placeholder="z. B. ART-1001" /></View>
                <View style={{ flex: 1 }}><Text style={styles.label}>EAN (optional)</Text><Input testID="p-ean" value={form.ean} onChangeText={set("ean")} placeholder="EAN" /></View>
              </View>
              <Text style={styles.label}>Kategorie</Text>
              <Pressable testID="p-category" style={styles.categoryPicker} onPress={() => setShowCategories((value) => !value)}><Text style={styles.categoryText}>{(categories.data ?? []).find((category: any) => category.id === form.categoryId)?.name ?? "Keine Kategorie"}</Text></Pressable>
              {showCategories ? <View style={{ gap: 4 }}><Pressable style={styles.categoryOption} onPress={() => { setForm((value) => ({ ...value, categoryId: "" })); setShowCategories(false); }}><Text style={styles.categoryText}>Keine Kategorie</Text></Pressable>{(categories.data ?? []).map((category: any) => <Pressable key={category.id} style={styles.categoryOption} onPress={() => { setForm((value) => ({ ...value, categoryId: category.id })); setShowCategories(false); }}><Text style={styles.categoryText}>{category.name}</Text></Pressable>)}<View style={styles.row}><Input value={newCategory} onChangeText={setNewCategory} placeholder="Neue Kategorie" style={{ flex: 1 }} /><Button title="Anlegen" kind="secondary" disabled={!newCategory.trim()} loading={createCategory.isPending} onPress={() => createCategory.mutate()} /></View></View> : null}

              <Text style={styles.label}>B2C-Shop-Collections</Text>
              <Muted>Ein Produkt kann in mehreren frei verwalteten Shopbereichen erscheinen.</Muted>
              <View style={styles.toggleGrid}>{(collections.data ?? []).map((collection: any) => { const selected = form.collectionIds.includes(collection.id); return <Pressable key={collection.id} onPress={() => setForm((value) => ({ ...value, collectionIds: selected ? value.collectionIds.filter((id) => id !== collection.id) : [...value.collectionIds, collection.id] }))} style={[styles.toggle, selected && styles.toggleActive]}><Text style={[styles.toggleText, selected && styles.toggleTextActive]}>{selected ? "✓ " : ""}{collection.name}</Text></Pressable>; })}</View>

              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Marke</Text>
                  {(brands.data ?? []).length ? <><Pressable testID="p-brand" style={styles.categoryPicker} onPress={() => setShowBrands((value) => !value)}><Text style={styles.categoryText}>{((brands.data ?? []).find((brand: any) => brand.id === form.brandId)?.name ?? form.brand) || "Keine Marke"}</Text></Pressable>{showBrands ? <View style={{ gap: 4 }}><Pressable style={styles.categoryOption} onPress={() => { setForm((value) => ({ ...value, brandId: "", brand: "" })); setShowBrands(false); }}><Text style={styles.categoryText}>Keine Marke</Text></Pressable>{(brands.data ?? []).filter((brand: any) => brand.active !== false).map((brand: any) => <Pressable key={brand.id} style={styles.categoryOption} onPress={() => { setForm((value) => ({ ...value, brandId: brand.id, brand: brand.name })); setShowBrands(false); }}><Text style={styles.categoryText}>{brand.name}</Text></Pressable>)}</View> : null}</> : <Input testID="p-brand" value={form.brand} onChangeText={set("brand")} placeholder="Marke in Einstellungen anlegen" />}
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Einheit</Text>
                  <Input testID="p-unit" value={form.unit} onChangeText={set("unit")} placeholder="kg" />
                </View>
              </View>
              <Text style={styles.label}>Bezeichnung</Text>
              <Input testID="p-name" value={form.name} onChangeText={set("name")} placeholder="z.B. Espresso Bar" />
              <View style={styles.row}><View style={{ flex: 1 }}><Text style={styles.label}>Shop-Slug</Text><Input value={form.slug} onChangeText={set("slug")} placeholder="wird automatisch erzeugt" autoCapitalize="none" /></View><View style={{ flex: 1 }}><Text style={styles.label}>Suchbegriffe</Text><Input value={form.searchKeywords} onChangeText={set("searchKeywords")} placeholder="durch Komma getrennt" /></View></View>

              <View style={styles.row}><View style={{ flex: 1 }}><Text style={styles.label}>Verpackungseinheit</Text><Input value={form.packagingUnit} onChangeText={set("packagingUnit")} placeholder="z. B. Karton" /></View><View style={{ flex: 1 }}><Text style={styles.label}>Menge je Gebinde</Text><Input value={form.packageQuantity} onChangeText={set("packageQuantity")} keyboardType="decimal-pad" placeholder="z. B. 6" /></View></View>
              <Text style={styles.label}>Palettenkonfiguration (B2B)</Text>
              <Muted>Direktes Gewicht je Palette oder Kartongewicht × Kartons je Palette hinterlegen. Fehlende Daten erzwingen eine Freigabe.</Muted>
              <View style={styles.row}><View style={{ flex: 1 }}><Text style={styles.label}>Einheiten je Karton</Text><Input value={form.unitsPerCase} onChangeText={set("unitsPerCase")} keyboardType="decimal-pad" /></View><View style={{ flex: 1 }}><Text style={styles.label}>kg je Karton</Text><Input value={form.kgPerCase} onChangeText={set("kgPerCase")} keyboardType="decimal-pad" /></View></View>
              <View style={styles.row}><View style={{ flex: 1 }}><Text style={styles.label}>Kartons je Palette</Text><Input value={form.casesPerPallet} onChangeText={set("casesPerPallet")} keyboardType="decimal-pad" /></View><View style={{ flex: 1 }}><Text style={styles.label}>kg je Palette</Text><Input value={form.kgPerPallet} onChangeText={set("kgPerPallet")} keyboardType="decimal-pad" /></View></View>
              <View style={styles.row}><View style={{ flex: 1 }}><Text style={styles.label}>Inhalt</Text><Input value={form.contentAmount} onChangeText={set("contentAmount")} keyboardType="decimal-pad" placeholder="z. B. 1" /></View><View style={{ flex: 1 }}><Text style={styles.label}>Inhaltseinheit</Text><Input value={form.contentUnit} onChangeText={set("contentUnit")} placeholder="kg, l, Stück" /></View><View style={{ flex: 1 }}><Text style={styles.label}>Mindestmenge</Text><Input value={form.minimumOrderQuantity} onChangeText={set("minimumOrderQuantity")} keyboardType="decimal-pad" /></View></View>

              <Text style={styles.label}>Verfügbarkeit & Aktionen</Text>
              <View style={styles.toggleGrid}>{[["b2bAvailable", "B2B verfügbar"], ["b2cAvailable", "B2C verfügbar"], ["directPurchaseAllowed", "Direktkauf"], ["financingRequestAllowed", "Finanzierungsanfrage"], ["quickAdd", "Quick Add"], ["subscriptionAllowed", "Abo möglich"]].map(([key, label]) => { const field = key as "b2bAvailable" | "b2cAvailable" | "directPurchaseAllowed" | "financingRequestAllowed" | "quickAdd" | "subscriptionAllowed"; return <Pressable key={key} onPress={() => setForm((value) => ({ ...value, [field]: !value[field] }))} style={[styles.toggle, form[field] && styles.toggleActive]}><Text style={[styles.toggleText, form[field] && styles.toggleTextActive]}>{form[field] ? "✓ " : ""}{label}</Text></Pressable>; })}</View>
              <Text style={styles.label}>Shop-Verfügbarkeit</Text><View style={styles.toggleGrid}>{[["available", "Verfügbar"], ["unavailable", "Nicht verfügbar"], ["preorder", "Vorbestellung"]].map(([key, label]) => <Pressable key={key} onPress={() => setForm((value) => ({ ...value, availability: key as Form["availability"] }))} style={[styles.toggle, form.availability === key && styles.toggleActive]}><Text style={[styles.toggleText, form.availability === key && styles.toggleTextActive]}>{label}</Text></Pressable>)}</View>
              {form.subscriptionAllowed ? <><Text style={styles.label}>Erlaubte Abo-Intervalle</Text><Input value={form.subscriptionIntervals} onChangeText={set("subscriptionIntervals")} placeholder="monthly" /></> : null}

              <Text style={styles.label}>Region / Herkunft</Text><View style={styles.toggleGrid}>{(regions.data ?? []).filter((row: any) => row.active !== false).map((row: any) => { const selected = form.regionIds.includes(row.id); return <Pressable key={row.id} onPress={() => setForm((value) => ({ ...value, regionIds: selected ? value.regionIds.filter((id) => id !== row.id) : [...value.regionIds, row.id] }))} style={[styles.toggle, selected && styles.toggleActive]}><Text style={[styles.toggleText, selected && styles.toggleTextActive]}>{row.name}</Text></Pressable>; })}</View>
              <Text style={styles.label}>Versandklasse</Text><View style={styles.toggleGrid}><Pressable onPress={() => setForm((value) => ({ ...value, shippingClassId: "" }))} style={[styles.toggle, !form.shippingClassId && styles.toggleActive]}><Text style={[styles.toggleText, !form.shippingClassId && styles.toggleTextActive]}>Keine</Text></Pressable>{(shippingClasses.data ?? []).filter((row: any) => row.active !== false).map((row: any) => <Pressable key={row.id} onPress={() => setForm((value) => ({ ...value, shippingClassId: row.id }))} style={[styles.toggle, form.shippingClassId === row.id && styles.toggleActive]}><Text style={[styles.toggleText, form.shippingClassId === row.id && styles.toggleTextActive]}>{row.name}</Text></Pressable>)}</View>

              {relevantAttributes.length ? <><Text style={styles.label}>Kategorieattribute</Text>{relevantAttributes.map((attribute: any) => <View key={attribute.id}><Muted>{attribute.name}</Muted>{attribute.valueType === "boolean" ? <View style={styles.toggleGrid}><Pressable style={[styles.toggle, form.attributeValues[attribute.key] === true && styles.toggleActive]} onPress={() => setForm((value) => ({ ...value, attributeValues: { ...value.attributeValues, [attribute.key]: true } }))}><Text style={styles.toggleText}>Ja</Text></Pressable><Pressable style={[styles.toggle, form.attributeValues[attribute.key] === false && styles.toggleActive]} onPress={() => setForm((value) => ({ ...value, attributeValues: { ...value.attributeValues, [attribute.key]: false } }))}><Text style={styles.toggleText}>Nein</Text></Pressable></View> : attribute.valueType === "select" || attribute.valueType === "multi_select" ? <View style={styles.toggleGrid}>{attribute.options.map((option: string) => { const selected = attribute.valueType === "multi_select" ? Array.isArray(form.attributeValues[attribute.key]) && (form.attributeValues[attribute.key] as string[]).includes(option) : form.attributeValues[attribute.key] === option; return <Pressable key={option} style={[styles.toggle, selected && styles.toggleActive]} onPress={() => setForm((value) => { const current = Array.isArray(value.attributeValues[attribute.key]) ? value.attributeValues[attribute.key] as string[] : []; return { ...value, attributeValues: { ...value.attributeValues, [attribute.key]: attribute.valueType === "multi_select" ? (selected ? current.filter((item) => item !== option) : [...current, option]) : option } }; })}><Text style={[styles.toggleText, selected && styles.toggleTextActive]}>{option}</Text></Pressable>; })}</View> : <Input value={String(form.attributeValues[attribute.key] ?? "")} onChangeText={(value) => setForm((current) => ({ ...current, attributeValues: { ...current.attributeValues, [attribute.key]: attribute.valueType === "number" ? Number(value.replace(",", ".")) : value } }))} keyboardType={attribute.valueType === "number" ? "decimal-pad" : "default"} />}</View>)}</> : null}

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

              <Text style={styles.label}>Varianten</Text><Muted>Varianten nutzen weiterhin den serverseitigen Produktpreis. Eigene Variantenpreise erfordern eine separate fachliche Freigabe.</Muted>
              {form.variants.map((variant, index) => <View key={variant.id || index} style={styles.variantBox}><View style={styles.row}><Input value={variant.name} onChangeText={(name) => setForm((value) => ({ ...value, variants: value.variants.map((item, i) => i === index ? { ...item, name } : item) }))} placeholder="Variantenname" style={{ flex: 1 }} /><Input value={variant.sku} onChangeText={(sku) => setForm((value) => ({ ...value, variants: value.variants.map((item, i) => i === index ? { ...item, sku } : item) }))} placeholder="SKU" style={{ flex: 1 }} /><Input value={variant.ean} onChangeText={(ean) => setForm((value) => ({ ...value, variants: value.variants.map((item, i) => i === index ? { ...item, ean } : item) }))} placeholder="EAN / GTIN" style={{ flex: 1 }} /></View><View style={styles.row}><Input value={variant.weight} onChangeText={(weight) => setForm((value) => ({ ...value, variants: value.variants.map((item, i) => i === index ? { ...item, weight } : item) }))} placeholder="Menge / Gewicht" keyboardType="decimal-pad" style={{ flex: 1 }} /><Input value={variant.unit} onChangeText={(unit) => setForm((value) => ({ ...value, variants: value.variants.map((item, i) => i === index ? { ...item, unit } : item) }))} placeholder="Einheit" style={{ flex: 1 }} /></View><Input value={variant.imageUrls} onChangeText={(imageUrls) => setForm((value) => ({ ...value, variants: value.variants.map((item, i) => i === index ? { ...item, imageUrls } : item) }))} placeholder="Bild-URLs, durch Komma getrennt" autoCapitalize="none" /><Text style={styles.label}>Verfügbarkeit</Text><View style={styles.toggleGrid}>{[["available", "Verfügbar"], ["preorder", "Vorbestellung"], ["unavailable", "Nicht verfügbar"]].map(([availability, label]) => <Pressable key={availability} style={[styles.toggle, variant.availability === availability && styles.toggleActive]} onPress={() => setForm((value) => ({ ...value, variants: value.variants.map((item, i) => i === index ? { ...item, availability: availability as VariantForm["availability"] } : item) }))}><Text style={[styles.toggleText, variant.availability === availability && styles.toggleTextActive]}>{label}</Text></Pressable>)}</View><View style={styles.actions}><Button title="Variante entfernen" kind="secondary" onPress={() => setForm((value) => ({ ...value, variants: value.variants.filter((_, i) => i !== index) }))} /></View></View>)}
              <Button title="Variante hinzufügen" kind="secondary" onPress={() => setForm((value) => ({ ...value, quickAdd: false, variants: [...value.variants, { id: `variant-${Date.now().toString(36)}`, name: "", sku: "", ean: "", weight: "", unit: "", imageUrls: "", availability: "available", active: true }] }))} />

              <Text style={styles.label}>Verknüpfte Produkte</Text><Muted>Adminverwaltete Empfehlungen und Kompatibilität – keine automatisch erfundenen Vorschläge.</Muted><RelationChoices title="Ähnliche Produkte" products={products.data ?? []} currentId={editingId} selected={form.relatedProductIds} onChange={(relatedProductIds) => setForm((value) => ({ ...value, relatedProductIds }))} /><RelationChoices title="Empfehlungen / Cross-Selling" products={products.data ?? []} currentId={editingId} selected={form.recommendedProductIds} onChange={(recommendedProductIds) => setForm((value) => ({ ...value, recommendedProductIds }))} /><RelationChoices title="Kompatible Produkte" products={products.data ?? []} currentId={editingId} selected={form.compatibleProductIds} onChange={(compatibleProductIds) => setForm((value) => ({ ...value, compatibleProductIds }))} />

              <Text style={styles.label}>Lebensmittelinformationen (nur explizit hinterlegte Angaben)</Text><Input value={String(form.foodInfo.ingredients ?? "")} onChangeText={(ingredients) => setForm((value) => ({ ...value, foodInfo: { ...value.foodInfo, ingredients } }))} placeholder="Zutaten" multiline /><Input value={String(form.foodInfo.allergens ?? "")} onChangeText={(allergens) => setForm((value) => ({ ...value, foodInfo: { ...value.foodInfo, allergens } }))} placeholder="Allergene" multiline /><Input value={String(form.foodInfo.responsibleOperator ?? "")} onChangeText={(responsibleOperator) => setForm((value) => ({ ...value, foodInfo: { ...value.foodInfo, responsibleOperator } }))} placeholder="Verantwortlicher Lebensmittelunternehmer" />

              <Text style={styles.label}>SEO</Text><Input value={form.seoTitle} onChangeText={set("seoTitle")} placeholder="SEO-Titel" /><Input value={form.seoDescription} onChangeText={set("seoDescription")} placeholder="Meta-Beschreibung" multiline />

              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>B2B-Ausgangspreis €</Text>
                  <Input testID="p-standard" value={form.standardPrice} onChangeText={set("standardPrice")} keyboardType="decimal-pad" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Einkaufspreis €</Text>
                  <Input testID="p-cost" value={form.cost} onChangeText={set("cost")} keyboardType="decimal-pad" />
                </View>
              </View>
              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Automatische Freigabegrenze € · INTERN</Text>
                  <Input testID="p-salesfloor" value={form.salesFloor} onChangeText={set("salesFloor")} keyboardType="decimal-pad" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Absolute Preisgrenze € · INTERN</Text>
                  <Input testID="p-absfloor" value={form.absoluteFloor} onChangeText={set("absoluteFloor")} keyboardType="decimal-pad" />
                </View>
              </View>

              <Text style={styles.label}>MwSt-Satz (%)</Text>
              <Input testID="p-tax" value={form.taxRate} onChangeText={set("taxRate")} keyboardType="number-pad" placeholder="z. B. 7" />

              <Text style={styles.label}>Lagerbestand (optional, leer = unbegrenzt)</Text>
              <Input testID="p-stock" value={form.stock} onChangeText={set("stock")} keyboardType="numeric" placeholder="z. B. 120" />

              <Text style={styles.label}>B2C-Shop-Preis € (brutto, leer = nicht im Shop)</Text>
              <Input testID="p-b2cprice" value={form.b2cPrice} onChangeText={set("b2cPrice")} keyboardType="decimal-pad" placeholder="z. B. 19,90" />

              <Text style={styles.label}>B2C-Mengenstaffeln (optional)</Text>
              <Muted>Ab welcher B2C-Menge gilt welcher Bruttopreis? B2B-Preise bleiben davon unberührt.</Muted>
              {form.b2cTiers.map((t, idx) => (
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
            const list = products.data ?? [];
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
                          B2B-Ausgang {euro(p.standardPrice)} · Auto-Freigabe {euro(p.salesFloor)} · absolute Grenze {euro(p.absoluteFloor)} · EK{" "}
                          {euro(p.cost)}
                        </Muted>
                        {p.b2cTiers?.length ? (
                          <Muted>
                            B2C-Staffel: {p.b2cTiers.map((t: any) => `ab ${t.minQty} → ${euro(t.price)}`).join(" · ")}
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

function RelationChoices({ title, products, currentId, selected, onChange }: { title: string; products: any[]; currentId: string | null; selected: string[]; onChange: (ids: string[]) => void }) {
  const styles = useStyles();
  return <View><Muted>{title}</Muted><View style={styles.toggleGrid}>{products.filter((product) => product.id !== currentId).map((product) => { const active = selected.includes(product.id); return <Pressable key={product.id} style={[styles.toggle, active && styles.toggleActive]} onPress={() => onChange(active ? selected.filter((id) => id !== product.id) : [...selected, product.id])}><Text style={[styles.toggleText, active && styles.toggleTextActive]}>{product.name}</Text></Pressable>; })}</View></View>;
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
  categoryPicker: { minHeight: 48, borderRadius: 12, backgroundColor: c.surfaceTertiary, paddingHorizontal: 14, justifyContent: "center" },
  categoryOption: { minHeight: 42, borderRadius: 10, backgroundColor: c.surfaceTertiary, paddingHorizontal: 14, justifyContent: "center" },
  categoryText: { color: c.onSurface, fontWeight: "700", fontSize: 14 },
  toggleGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  toggle: { minHeight: 40, paddingHorizontal: 12, borderRadius: 10, borderWidth: 1, borderColor: c.border, justifyContent: "center", backgroundColor: c.surfaceTertiary },
  toggleActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  toggleText: { color: c.onSurfaceSecondary, fontSize: 13, fontWeight: "700" },
  toggleTextActive: { color: c.onBrandPrimary },
  variantBox: { gap: 8, padding: 12, borderRadius: 12, backgroundColor: c.surfaceTertiary, marginTop: 8 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
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
  galleryAdmin: { gap: 10, marginTop: 10 },
  galleryHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  galleryGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  galleryItem: { width: 180, gap: 7, padding: 8, borderRadius: 12, borderWidth: 1, borderColor: c.border, backgroundColor: c.surfaceTertiary },
  galleryImage: { width: "100%", height: 110, borderRadius: 9, backgroundColor: c.surface },
  galleryBadge: { color: c.onSurfaceSecondary, fontSize: 12, fontWeight: "800" },
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
