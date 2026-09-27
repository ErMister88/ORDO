import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { ArrowLeft, Funnel, ShoppingCart, X } from "phosphor-react-native";
import { Modal, Pressable, ScrollView, View, useWindowDimensions } from "react-native";

import { apiGet, fileUrl } from "@/src/api/client";
import { Button, EmptyState, ErrorState, Input, LoadingState, Muted, PageContainer, SectionTitle } from "@/src/components/ui";
import { LocalizedText as Text, useI18n } from "@/src/i18n";
import { ProductCard, SearchBox } from "@/src/shop/commerce";
import { useCart } from "@/src/shop/cart";
import { useCommerceMetadata } from "@/src/shop/seo";
import { makeStyles, tokens, useTheme } from "@/src/theme";

type Dimension = "category" | "brand" | "collection" | "region" | "search";

export function CatalogScreen({ dimension, reference, query = "" }: { dimension: Dimension; reference?: string; query?: string }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { width } = useWindowDimensions();
  const { tf } = useI18n();
  const router = useRouter();
  const cart = useCart();
  const [sort, setSort] = useState("relevance");
  const [availability, setAvailability] = useState("");
  const [brandFilter, setBrandFilter] = useState("");
  const [collectionFilter, setCollectionFilter] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [page, setPage] = useState(1);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [attributeFilters, setAttributeFilters] = useState<Record<string, string>>({});
  const discovery = useQuery({ queryKey: ["shop-discovery"], queryFn: () => apiGet("/shop/discovery") });
  const params = new URLSearchParams({ sort, pageSize: "24", page: String(page) });
  if (dimension === "search") params.set("q", query);
  else if (reference) params.set(dimension, reference);
  if (availability) params.set("availability", availability);
  if (brandFilter && dimension !== "brand") params.set("brand", brandFilter);
  if (collectionFilter && dimension !== "collection") params.set("collection", collectionFilter);
  if (minPrice && Number(minPrice.replace(",", ".")) >= 0) params.set("minPrice", String(Math.round(Number(minPrice.replace(",", ".")) * 100)));
  if (maxPrice && Number(maxPrice.replace(",", ".")) >= 0) params.set("maxPrice", String(Math.round(Number(maxPrice.replace(",", ".")) * 100)));
  Object.entries(attributeFilters).forEach(([key, value]) => value && params.append("attribute", `${key}:${value}`));
  const catalog = useQuery({ queryKey: ["shop-catalog", params.toString()], queryFn: () => apiGet(`/shop/catalog?${params}`) });
  const current = useMemo(() => {
    const key = dimension === "category" ? "categories" : dimension === "brand" ? "brands" : dimension === "collection" ? "collections" : dimension === "region" ? "regions" : null;
    return key ? (discovery.data?.[key] ?? []).find((item: any) => item.slug === reference || item.id === reference) : null;
  }, [dimension, discovery.data, reference]);
  const children = dimension === "category" && current ? (discovery.data?.categories ?? []).filter((item: any) => item.parentId === current.id) : [];
  const canonicalPath = dimension === "category" ? `/shop/kategorie/${reference ?? ""}` : dimension === "brand" ? `/marken/${reference ?? ""}` : dimension === "collection" ? `/collections/${reference ?? ""}` : dimension === "region" ? `/regionen/${reference ?? ""}` : `/shop/suche?q=${encodeURIComponent(query)}`;
  useCommerceMetadata({ title: current ? current.seoTitle || `${current.name} | ORDO` : dimension === "search" ? tf("Suchergebnisse für „{query}“", { query }) : undefined, description: current?.seoDescription || current?.description || "", path: canonicalPath, image: current?.imageUrl ? fileUrl(current.imageUrl) : "" });
  useEffect(() => {
    setSort(current?.defaultSort ?? "relevance");
  }, [current?.defaultSort, current?.id]);
  useEffect(() => { setPage(1); }, [dimension, query, reference]);
  const filterPanel = <FilterPanel
    filters={catalog.data?.filters ?? []}
    brands={dimension === "brand" ? [] : discovery.data?.brands ?? []}
    collections={dimension === "collection" ? [] : discovery.data?.collections ?? []}
    availability={availability}
    setAvailability={(value) => { setAvailability(value); setPage(1); }}
    brand={brandFilter}
    setBrand={(value) => { setBrandFilter(value); setPage(1); }}
    collection={collectionFilter}
    setCollection={(value) => { setCollectionFilter(value); setPage(1); }}
    minPrice={minPrice}
    maxPrice={maxPrice}
    setMinPrice={(value) => { setMinPrice(value); setPage(1); }}
    setMaxPrice={(value) => { setMaxPrice(value); setPage(1); }}
    values={attributeFilters}
    setValues={(value) => { setAttributeFilters(value); setPage(1); }}
  />;
  return <View style={styles.root}>
    <View style={styles.header}>
      <Pressable style={styles.iconButton} onPress={() => router.back()}><ArrowLeft size={21} color={colors.onSurface} /></Pressable>
      <View style={{ flex: 1 }}><Text style={styles.headerTitle}>{dimension === "search" ? tf("Suchergebnisse für „{query}“", { query }) : current?.name || "Shop"}</Text><Muted>{catalog.data?.total ?? 0} Produkte</Muted></View>
      <Pressable style={styles.cartButton} onPress={() => router.push("/shop/warenkorb")}><ShoppingCart size={21} color={colors.onBrandPrimary} />{cart.count ? <Text style={styles.badge}>{cart.count}</Text> : null}</Pressable>
    </View>
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled"><PageContainer style={styles.page}>
      <SearchBox initial={dimension === "search" ? query : ""} />
      {current?.description ? <Text style={styles.description}>{current.description}</Text> : null}
      {children.length ? <ScrollView horizontal contentContainerStyle={styles.children} showsHorizontalScrollIndicator={false}>{children.map((child: any) => <Pressable key={child.id} style={styles.child} onPress={() => router.push(`/shop/kategorie/${child.slug || child.id}`)}><Text style={styles.childText}>{child.name}</Text></Pressable>)}</ScrollView> : null}
      <View style={styles.toolbar}>{width < 1024 ? <Pressable style={styles.filterButton} onPress={() => setFiltersOpen(true)}><Funnel size={18} color={colors.onSurface} /><Text style={styles.filterText}>Filter</Text></Pressable> : null}<ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sorts}>{[["relevance", "Relevanz"], ["newest", "Neuheiten"], ["price_asc", "Preis aufsteigend"], ["price_desc", "Preis absteigend"], ["name", "Name"]].map(([value, label]) => <Pressable key={value} style={[styles.sort, sort === value && styles.sortActive]} onPress={() => { setSort(value); setPage(1); }}><Text style={[styles.sortText, sort === value && styles.sortTextActive]}>{label}</Text></Pressable>)}</ScrollView></View>
      <View style={styles.body}>{width >= 1024 ? <View style={styles.desktopFilters}>{filterPanel}</View> : null}<View style={styles.results}>{catalog.isLoading ? <LoadingState /> : catalog.isError ? <ErrorState message="Produkte konnten nicht geladen werden" onRetry={() => { void catalog.refetch(); }} /> : (catalog.data?.items ?? []).length ? <View style={styles.grid}>{catalog.data.items.map((product: any) => <ProductCard key={product.id} product={product} />)}</View> : <EmptyState title="Keine Produkte gefunden" subtitle={dimension === "search" ? tf("Für „{query}“ gibt es aktuell keine Treffer. Probiere einen anderen Begriff oder öffne eine Kategorie.", { query }) : "Entferne Filter oder wähle eine andere Kategorie."} />}</View></View>
      {(catalog.data?.pages ?? 0) > 1 ? <View style={styles.pagination}><Button title="Zurück" kind="secondary" disabled={page <= 1} onPress={() => setPage((value) => Math.max(1, value - 1))} /><Text style={styles.pageText}>{tf("Seite {page} von {pages}", { page, pages: catalog.data.pages })}</Text><Button title="Weiter" kind="secondary" disabled={page >= catalog.data.pages} onPress={() => setPage((value) => value + 1)} /></View> : null}
      {dimension === "search" && !catalog.isLoading && !(catalog.data?.items ?? []).length ? <Button title="Alle Kategorien anzeigen" onPress={() => router.replace("/shop")} /> : null}
    </PageContainer></ScrollView>
    <Modal visible={filtersOpen && width < 1024} transparent animationType="slide" onRequestClose={() => setFiltersOpen(false)}><View style={styles.modalBackdrop}><View style={styles.drawer}><View style={styles.drawerHead}><SectionTitle>Filter</SectionTitle><Pressable onPress={() => setFiltersOpen(false)}><X size={24} color={colors.onSurface} /></Pressable></View><ScrollView>{filterPanel}</ScrollView><Button title="Ergebnisse anzeigen" onPress={() => setFiltersOpen(false)} /></View></View></Modal>
  </View>;
}

function FilterPanel({ filters, brands, collections, availability, setAvailability, brand, setBrand, collection, setCollection, minPrice, maxPrice, setMinPrice, setMaxPrice, values, setValues }: { filters: any[]; brands: any[]; collections: any[]; availability: string; setAvailability: (value: string) => void; brand: string; setBrand: (value: string) => void; collection: string; setCollection: (value: string) => void; minPrice: string; maxPrice: string; setMinPrice: (value: string) => void; setMaxPrice: (value: string) => void; values: Record<string, string>; setValues: (value: Record<string, string>) => void }) {
  const styles = useStyles();
  return <View style={styles.panel}><Text style={styles.panelTitle}>Verfügbarkeit</Text>{[["", "Alle"], ["available", "Verfügbar"], ["preorder", "Vorbestellung"]].map(([value, label]) => <Pressable key={value} style={[styles.filterChoice, availability === value && styles.filterChoiceActive]} onPress={() => setAvailability(value)}><Text style={styles.filterChoiceText}>{label}</Text></Pressable>)}{brands.length ? <View><Text style={styles.panelTitle}>Marke</Text><Pressable style={[styles.filterChoice, !brand && styles.filterChoiceActive]} onPress={() => setBrand("")}><Text style={styles.filterChoiceText}>Alle</Text></Pressable>{brands.map((item) => <Pressable key={item.id} style={[styles.filterChoice, brand === item.id && styles.filterChoiceActive]} onPress={() => setBrand(item.id)}><Text style={styles.filterChoiceText}>{item.name}</Text></Pressable>)}</View> : null}{collections.length ? <View><Text style={styles.panelTitle}>Collection</Text><Pressable style={[styles.filterChoice, !collection && styles.filterChoiceActive]} onPress={() => setCollection("")}><Text style={styles.filterChoiceText}>Alle</Text></Pressable>{collections.map((item) => <Pressable key={item.id} style={[styles.filterChoice, collection === item.id && styles.filterChoiceActive]} onPress={() => setCollection(item.id)}><Text style={styles.filterChoiceText}>{item.name}</Text></Pressable>)}</View> : null}<Text style={styles.panelTitle}>Preis</Text><View style={styles.priceRow}><Input value={minPrice} onChangeText={setMinPrice} placeholder="Von €" keyboardType="decimal-pad" style={{ flex: 1 }} /><Input value={maxPrice} onChangeText={setMaxPrice} placeholder="Bis €" keyboardType="decimal-pad" style={{ flex: 1 }} /></View>{filters.filter((item) => item.filterable && item.public !== false).map((filter) => <View key={filter.id}><Text style={styles.panelTitle}>{filter.name}</Text>{filter.valueType === "boolean" ? [["", "Alle"], ["true", "Ja"], ["false", "Nein"]].map(([value, label]) => <Pressable key={value} style={[styles.filterChoice, (values[filter.key] ?? "") === value && styles.filterChoiceActive]} onPress={() => setValues({ ...values, [filter.key]: value })}><Text style={styles.filterChoiceText}>{label}</Text></Pressable>) : filter.options?.length ? <><Pressable style={[styles.filterChoice, !values[filter.key] && styles.filterChoiceActive]} onPress={() => setValues({ ...values, [filter.key]: "" })}><Text style={styles.filterChoiceText}>Alle</Text></Pressable>{filter.options.map((option: string) => <Pressable key={option} style={[styles.filterChoice, values[filter.key] === option && styles.filterChoiceActive]} onPress={() => setValues({ ...values, [filter.key]: option })}><Text style={styles.filterChoiceText}>{option}</Text></Pressable>)}</> : filter.valueType === "number" ? <Input value={values[filter.key] ?? ""} onChangeText={(value) => setValues({ ...values, [filter.key]: value })} placeholder="Wert" keyboardType="decimal-pad" /> : null}</View>)}</View>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary }, header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconButton: { width: 44, height: 44, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" }, cartButton: { width: 48, height: 44, borderRadius: 12, backgroundColor: c.brandPrimary, alignItems: "center", justifyContent: "center" }, badge: { position: "absolute", right: -4, top: -5, minWidth: 19, textAlign: "center", color: c.onError, backgroundColor: c.error, borderRadius: 10, overflow: "hidden", fontSize: 11, fontWeight: "900" },
  headerTitle: { color: c.onSurface, fontSize: 20, fontWeight: "900" }, content: { padding: tokens.spacing.lg, paddingBottom: 48 }, page: { gap: 15 }, description: { color: c.onSurfaceSecondary, lineHeight: 21 }, children: { gap: 8 }, child: { paddingVertical: 10, paddingHorizontal: 15, backgroundColor: c.surface, borderRadius: 999, borderWidth: 1, borderColor: c.border }, childText: { color: c.onSurface, fontWeight: "800" },
  toolbar: { flexDirection: "row", alignItems: "center", gap: 10 }, filterButton: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border, borderRadius: 10, paddingHorizontal: 12, height: 42 }, filterText: { color: c.onSurface, fontWeight: "800" }, sorts: { gap: 7 }, sort: { paddingHorizontal: 12, height: 40, justifyContent: "center", backgroundColor: c.surface, borderRadius: 9, borderWidth: 1, borderColor: c.border }, sortActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary }, sortText: { color: c.onSurfaceSecondary, fontSize: 12, fontWeight: "700" }, sortTextActive: { color: c.onBrandPrimary },
  body: { flexDirection: "row", alignItems: "flex-start", gap: 18 }, desktopFilters: { width: 220 }, results: { flex: 1 }, grid: { flexDirection: "row", flexWrap: "wrap", gap: 14 }, panel: { gap: 7 }, panelTitle: { color: c.onSurface, fontWeight: "900", marginTop: 10, marginBottom: 2 }, filterChoice: { paddingVertical: 9, paddingHorizontal: 11, borderRadius: 8 }, filterChoiceActive: { backgroundColor: c.brandTertiary }, filterChoiceText: { color: c.onSurfaceSecondary, fontWeight: "700" }, priceRow: { flexDirection: "row", gap: 8 }, pagination: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 12 }, pageText: { color: c.onSurface, fontWeight: "800" }, modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.3)", justifyContent: "flex-end" }, drawer: { backgroundColor: c.surface, maxHeight: "84%", borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 20, gap: 14 }, drawerHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
}));
