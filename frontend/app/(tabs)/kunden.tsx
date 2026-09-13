import { useMemo, useState } from "react";
import { View, Text, FlatList, Pressable } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { MagnifyingGlass, ArrowRight, MapPin } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet } from "@/src/api/client";
import { num } from "@/src/lib/format";
import { ScreenHeader } from "@/src/components/screen-header";
import { Input, EmptyState } from "@/src/components/ui";

const FILTERS = [
  { key: "alle", label: "Alle" },
  { key: "ueberfaellig", label: "Überfällig" },
  { key: "aktiv", label: "Aktuell" },
];

export default function Kunden() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("alle");

  const { data, isLoading } = useQuery({ queryKey: ["companies"], queryFn: () => apiGet("/companies") });

  const list = useMemo(() => {
    let items = data ?? [];
    if (filter === "ueberfaellig") items = items.filter((c: any) => c.overdue);
    if (filter === "aktiv") items = items.filter((c: any) => !c.overdue);
    const s = q.trim().toLowerCase();
    if (s) items = items.filter((c: any) => c.name.toLowerCase().includes(s) || c.city.toLowerCase().includes(s));
    return items;
  }, [data, q, filter]);

  return (
    <View style={styles.root}>
      <ScreenHeader title="Kunden" subtitle={`${data?.length ?? 0} Unternehmen`} />

      <View style={styles.stickyTop}>
        <View style={styles.searchWrap}>
          <MagnifyingGlass size={18} color={colors.muted} />
          <Input
            testID="customer-search-input"
            value={q}
            onChangeText={setQ}
            placeholder="Kunde oder Ort suchen"
            style={styles.searchInput}
          />
        </View>
        <View style={styles.chipRowWrap}>
          <FlatList
            horizontal
            data={FILTERS}
            keyExtractor={(f) => f.key}
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chipRow}
            renderItem={({ item }) => {
              const active = filter === item.key;
              return (
                <Pressable
                  testID={`filter-${item.key}`}
                  onPress={() => setFilter(item.key)}
                  style={[styles.chip, active && styles.chipActive]}
                >
                  <Text style={[styles.chipText, active && styles.chipTextActive]}>{item.label}</Text>
                </Pressable>
              );
            }}
          />
        </View>
      </View>

      <FlatList
        data={list}
        keyExtractor={(c: any) => c.id}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
        ListEmptyComponent={
          !isLoading ? <EmptyState title="Keine Kunden gefunden" subtitle="Passen Sie Suche oder Filter an" /> : null
        }
        renderItem={({ item }) => (
          <Pressable
            testID={`customer-row-${item.id}`}
            style={styles.row}
            onPress={() => router.push(`/kunde/${item.id}`)}
          >
            <View style={{ flex: 1 }}>
              <Text style={styles.rowName}>{item.name}</Text>
              <View style={styles.rowMeta}>
                <MapPin size={13} color={colors.muted} />
                <Text style={styles.rowSub}>
                  {item.city} · {num(item.monthlyKg)} kg/Monat
                </Text>
              </View>
            </View>
            <View style={styles.rowRight}>
              <View style={styles.statusInline}>
                <View
                  style={[styles.statusDot, { backgroundColor: item.overdue ? colors.error : colors.success }]}
                />
                <Text style={[styles.statusText, { color: item.overdue ? colors.error : colors.success }]}>
                  {item.overdue ? "Überfällig" : "Aktuell"}
                </Text>
              </View>
              <ArrowRight size={18} color={colors.muted} />
            </View>
          </Pressable>
        )}
      />
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  stickyTop: {
    backgroundColor: c.surface,
    borderBottomWidth: 1,
    borderBottomColor: c.divider,
    paddingBottom: 8,
  },
  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: c.surfaceTertiary,
    borderRadius: 14,
    paddingHorizontal: 14,
    marginHorizontal: 20,
    marginTop: 12,
    gap: 8,
  },
  searchInput: { flex: 1, backgroundColor: "transparent", borderWidth: 0, paddingHorizontal: 0 },
  chipRowWrap: { height: 56, justifyContent: "center" },
  chipRow: { paddingHorizontal: 20, gap: 8, alignItems: "center" },
  chip: {
    flexShrink: 0,
    height: 36,
    paddingHorizontal: 16,
    borderRadius: 999,
    backgroundColor: c.surfaceTertiary,
    borderWidth: 1,
    borderColor: c.border,
    alignItems: "center",
    justifyContent: "center",
  },
  chipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  chipText: { fontSize: 13, fontWeight: "700", color: c.onSurfaceTertiary },
  chipTextActive: { color: c.onBrandPrimary },
  listContent: { padding: 20, paddingTop: 12, gap: 10, paddingBottom: 32 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: c.surface,
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: c.border,
    gap: 10,
    minHeight: 64,
  },
  rowName: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  rowMeta: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 3 },
  rowSub: { fontSize: 13, color: c.muted },
  rowRight: { alignItems: "flex-end", gap: 6 },
  statusInline: { flexDirection: "row", alignItems: "center", gap: 5 },
  statusDot: { width: 7, height: 7, borderRadius: 999 },
  statusText: { fontSize: 12, fontWeight: "700" },
}));
