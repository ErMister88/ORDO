import { useMemo, useState } from "react";
import {
  View,
  FlatList,
  Pressable,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MagnifyingGlass, ArrowRight, MapPin, Plus, X } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet } from "@/src/api/client";
import { apiPost } from "@/src/api/client";
import { useAuth } from "@/src/auth/auth";
import { num } from "@/src/lib/format";
import { ScreenHeader } from "@/src/components/screen-header";
import { Button, Card, Input, EmptyState } from "@/src/components/ui";
import { LocalizedText as Text, useI18n } from "@/src/i18n";

const FILTERS = [
  { key: "alle", label: "Alle" },
  { key: "ueberfaellig", label: "Überfällig" },
  { key: "aktiv", label: "Aktuell" },
];

export default function Kunden() {
  useI18n();
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const params = useLocalSearchParams<{ new?: string }>();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("alle");
  const [salesFilter, setSalesFilter] = useState("all");
  const [showCreate, setShowCreate] = useState(params.new === "1");
  const [createError, setCreateError] = useState("");
  const emptyForm = { name: "", street: "", houseNumber: "", zip: "", city: "", country: "Deutschland", email: "", phone: "", vatId: "", taxNumber: "", status: "Lead", customerTypeId: "", customerTagIds: [] as string[], contactFirstName: "", contactLastName: "", contactTitle: "", contactPhone: "", contactMobile: "", contactEmail: "" };
  const [form, setForm] = useState(emptyForm);

  const { data, isLoading } = useQuery({ queryKey: ["companies"], queryFn: () => apiGet("/companies") });
  const salesStaff = useQuery({ queryKey: ["sales-staff"], queryFn: () => apiGet("/staff/sales"), enabled: user?.role === "admin" });
  const customerTypes = useQuery({ queryKey: ["config-customer-types"], queryFn: () => apiGet("/business-config/customer-types") });
  const customerTags = useQuery({ queryKey: ["config-customer-tags"], queryFn: () => apiGet("/business-config/customer-tags") });
  const createCustomer = useMutation({
    mutationFn: () => apiPost("/companies", {
      name: form.name, city: form.city, email: form.email, phone: form.phone, vatId: form.vatId,
      taxNumber: form.taxNumber, status: form.status,
      customerTypeId: form.customerTypeId || null, customerTagIds: form.customerTagIds,
      primaryAddress: { type: "main", label: "Hauptadresse", street: form.street, houseNumber: form.houseNumber, zip: form.zip, city: form.city, country: form.country, active: true },
      ...(form.contactFirstName && form.contactLastName && form.contactEmail ? { primaryContact: { firstName: form.contactFirstName, lastName: form.contactLastName, title: form.contactTitle, phone: form.contactPhone, mobile: form.contactMobile, email: form.contactEmail, active: true } } : {}),
    }),
    onSuccess: (company: any) => {
      qc.invalidateQueries({ queryKey: ["companies"] });
      setShowCreate(false);
      setForm(emptyForm);
      router.push(`/kunde/${company.id}`);
    },
    onError: (error: Error) => setCreateError(error.message),
  });

  const list = useMemo(() => {
    let items = data ?? [];
    if (filter === "ueberfaellig") items = items.filter((c: any) => c.overdue);
    if (filter === "aktiv") items = items.filter((c: any) => !c.overdue);
    if (salesFilter === "none") items = items.filter((c: any) => !c.assignedSalesRepId);
    if (salesFilter !== "all" && salesFilter !== "none") items = items.filter((c: any) => c.assignedSalesRepId === salesFilter);
    const s = q.trim().toLowerCase();
    if (s) items = items.filter((c: any) => c.name.toLowerCase().includes(s) || c.city.toLowerCase().includes(s));
    return items;
  }, [data, q, filter, salesFilter]);

  return (
    <View style={styles.root}>
      <ScreenHeader title="Kunden" subtitle={`${data?.length ?? 0} Unternehmen`} />

      <View style={styles.actionBar}>
        <Pressable testID="create-customer-button" style={styles.createButton} onPress={() => setShowCreate((value) => !value)}>
          {showCreate ? <X size={17} color={colors.onBrandPrimary} weight="bold" /> : <Plus size={17} color={colors.onBrandPrimary} weight="bold" />}
          <Text style={styles.createButtonText}>{showCreate ? "Schließen" : "Neuer Kunde"}</Text>
        </Pressable>
      </View>

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
        {user?.role === "admin" ? <View style={styles.chipRowWrap}><FlatList horizontal data={[{ id: "all", name: "Alle Vertriebler" }, ...(salesStaff.data ?? []), { id: "none", name: "Kein Vertriebler" }]} keyExtractor={(row: any) => row.id} showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow} renderItem={({ item }: any) => <Pressable onPress={() => setSalesFilter(item.id)} style={[styles.chip, salesFilter === item.id && styles.chipActive]}><Text style={[styles.chipText, salesFilter === item.id && styles.chipTextActive]}>{item.name}</Text></Pressable>} /></View> : null}
      </View>

      <FlatList
        data={list}
        keyExtractor={(c: any) => c.id}
        contentContainerStyle={styles.listContent}
        showsVerticalScrollIndicator={false}
        ListHeaderComponent={showCreate ? (
          <Card testID="create-customer-form" style={styles.createCard}>
            <Text style={styles.formTitle}>B2B-Kunde anlegen</Text>
            <Input testID="customer-name" value={form.name} onChangeText={(name) => setForm((value) => ({ ...value, name }))} placeholder="Unternehmen" />
            <Text style={styles.formHint}>Hauptadresse</Text>
            <View style={styles.formRow}>
              <Input value={form.street} onChangeText={(street) => setForm((value) => ({ ...value, street }))} placeholder="Straße" style={styles.formInput} />
              <Input value={form.houseNumber} onChangeText={(houseNumber) => setForm((value) => ({ ...value, houseNumber }))} placeholder="Hausnummer" style={{ flex: 0.45 }} />
            </View>
            <View style={styles.formRow}>
              <Input value={form.zip} onChangeText={(zip) => setForm((value) => ({ ...value, zip }))} placeholder="PLZ" style={{ flex: 0.45 }} />
              <Input testID="customer-city" value={form.city} onChangeText={(city) => setForm((value) => ({ ...value, city }))} placeholder="Ort" style={styles.formInput} />
            </View>
            <Input value={form.country} onChangeText={(country) => setForm((value) => ({ ...value, country }))} placeholder="Land" />
            <View style={styles.formRow}>
              <Input testID="customer-email" value={form.email} onChangeText={(email) => setForm((value) => ({ ...value, email }))} placeholder="E-Mail" autoCapitalize="none" style={styles.formInput} />
              <Input testID="customer-phone" value={form.phone} onChangeText={(phone) => setForm((value) => ({ ...value, phone }))} placeholder="Telefon" style={styles.formInput} />
            </View>
            <View style={styles.formRow}>
              <Input testID="customer-vat" value={form.vatId} onChangeText={(vatId) => setForm((value) => ({ ...value, vatId }))} placeholder="USt-ID (optional)" style={styles.formInput} />
              <Input value={form.taxNumber} onChangeText={(taxNumber) => setForm((value) => ({ ...value, taxNumber }))} placeholder="Steuernummer (optional)" style={styles.formInput} />
            </View>
            {(customerTypes.data ?? []).some((entry: any) => entry.active !== false) ? <><Text style={styles.formHint}>Kundentyp (optional)</Text><View style={styles.selectionWrap}>{(customerTypes.data ?? []).filter((entry: any) => entry.active !== false).map((entry: any) => <Pressable key={entry.id} onPress={() => setForm((value) => ({ ...value, customerTypeId: value.customerTypeId === entry.id ? "" : entry.id }))} style={[styles.chip, form.customerTypeId === entry.id && styles.chipActive]}><Text style={[styles.chipText, form.customerTypeId === entry.id && styles.chipTextActive]}>{entry.name}</Text></Pressable>)}</View></> : null}
            {(customerTags.data ?? []).some((entry: any) => entry.active !== false) ? <><Text style={styles.formHint}>Klassifizierungen (optional, mehrere möglich)</Text><View style={styles.selectionWrap}>{(customerTags.data ?? []).filter((entry: any) => entry.active !== false).map((entry: any) => { const active = form.customerTagIds.includes(entry.id); return <Pressable key={entry.id} onPress={() => setForm((value) => ({ ...value, customerTagIds: active ? value.customerTagIds.filter((id) => id !== entry.id) : [...value.customerTagIds, entry.id] }))} style={[styles.chip, active && styles.chipActive]}><Text style={[styles.chipText, active && styles.chipTextActive]}>{entry.name}</Text></Pressable>; })}</View></> : null}
            <Text style={styles.formHint}>Erster Ansprechpartner (optional)</Text>
            <View style={styles.formRow}><Input value={form.contactFirstName} onChangeText={(contactFirstName) => setForm((value) => ({ ...value, contactFirstName }))} placeholder="Vorname" style={styles.formInput} /><Input value={form.contactLastName} onChangeText={(contactLastName) => setForm((value) => ({ ...value, contactLastName }))} placeholder="Nachname" style={styles.formInput} /></View>
            <Input value={form.contactTitle} onChangeText={(contactTitle) => setForm((value) => ({ ...value, contactTitle }))} placeholder="Funktion / Rolle" />
            <View style={styles.formRow}><Input value={form.contactPhone} onChangeText={(contactPhone) => setForm((value) => ({ ...value, contactPhone }))} placeholder="Telefon" style={styles.formInput} /><Input value={form.contactMobile} onChangeText={(contactMobile) => setForm((value) => ({ ...value, contactMobile }))} placeholder="Mobil" style={styles.formInput} /></View>
            <Input value={form.contactEmail} onChangeText={(contactEmail) => setForm((value) => ({ ...value, contactEmail }))} placeholder="E-Mail Ansprechpartner" autoCapitalize="none" />
            <Text style={styles.formHint}>{user?.role === "sales" ? "Sie werden automatisch als zuständiger Vertrieb eingetragen." : "Der Kunde startet ohne Vertriebszuordnung."}</Text>
            {createError ? <Text style={styles.formError}>{createError}</Text> : null}
            <Button title="Kunde anlegen" loading={createCustomer.isPending} disabled={!form.name.trim() || !form.street.trim() || !form.zip.trim() || !form.city.trim()} onPress={() => { setCreateError(""); createCustomer.mutate(); }} />
          </Card>
        ) : null}
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
              <Text style={styles.crmStatus}>{item.status ?? "Aktiv"}</Text>
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
  actionBar: { backgroundColor: c.surface, paddingHorizontal: 20, paddingTop: 10, alignItems: "flex-end" },
  createButton: { flexDirection: "row", alignItems: "center", gap: 7, backgroundColor: c.brandPrimary, borderRadius: 12, paddingHorizontal: 15, minHeight: 42 },
  createButtonText: { color: c.onBrandPrimary, fontWeight: "800", fontSize: 14 },
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
  createCard: { marginBottom: 12 },
  formTitle: { fontSize: 17, fontWeight: "800", color: c.onSurface },
  formRow: { flexDirection: "row", gap: 10 },
  formInput: { flex: 1 },
  formHint: { fontSize: 13, color: c.muted },
  selectionWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  formError: { fontSize: 13, color: c.error, fontWeight: "700" },
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
  crmStatus: { fontSize: 12, color: c.brandPrimary, fontWeight: "800", marginTop: 4 },
  rowRight: { alignItems: "flex-end", gap: 6 },
  statusInline: { flexDirection: "row", alignItems: "center", gap: 5 },
  statusDot: { width: 7, height: 7, borderRadius: 999 },
  statusText: { fontSize: 12, fontWeight: "700" },
}));
