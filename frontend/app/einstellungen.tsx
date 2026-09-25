import { useState } from "react";
import {
  Pressable,
  ScrollView,
  View,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { ArrowLeft, PencilSimple } from "phosphor-react-native";

import { apiDelete, apiGet, apiPost, apiPut } from "@/src/api/client";
import { makeStyles, tokens, useTheme } from "@/src/theme";
import { Button, Card, Input, Muted, SectionTitle } from "@/src/components/ui";
import { LocalizedText as Text, localizedAlert } from "@/src/i18n";

type Entry = { id: string; name: string; description?: string; imageUrl?: string; sortOrder?: number; active?: boolean };
type SectionSpec = { title: string; help: string; path: string; queryKey: string };

const SECTIONS: SectionSpec[] = [
  { title: "Marken", help: "Hersteller und Handelsmarken für Produkte.", path: "/business-config/brands", queryKey: "config-brands" },
  { title: "Produktkategorien", help: "Fachliche Sortimentsbereiche unabhängig vom Shop.", path: "/product-categories", queryKey: "product-categories" },
  { title: "B2C-Collections", help: "Kuratierte Bereiche, in denen Produkte im Shop erscheinen.", path: "/shop-collections", queryKey: "shop-collections-admin" },
  { title: "Kundentypen", help: "Ein primärer Geschäftstyp pro Kunde, ohne Einfluss auf Preise.", path: "/business-config/customer-types", queryKey: "config-customer-types" },
  { title: "Klassifizierungen", help: "Mehrere frei kombinierbare Merkmale pro Kunde.", path: "/business-config/customer-tags", queryKey: "config-customer-tags" },
];

function ConfigSection({ spec }: { spec: SectionSpec }) {
  const styles = useStyles();
  const qc = useQueryClient();
  const query = useQuery<Entry[]>({ queryKey: [spec.queryKey], queryFn: () => apiGet(spec.path) });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sortOrder, setSortOrder] = useState("0");
  const [message, setMessage] = useState("");

  const reset = () => { setEditingId(null); setName(""); setDescription(""); setSortOrder("0"); };
  const save = useMutation({
    mutationFn: () => {
      const editingEntry = (query.data ?? []).find((entry) => entry.id === editingId);
      const body = {
        name, description, sortOrder: Number(sortOrder) || 0, active: true,
        ...(spec.path === "/shop-collections" ? { imageUrl: editingEntry?.imageUrl ?? "" } : {}),
      };
      return editingId ? apiPut(`${spec.path}/${editingId}`, body) : apiPost(spec.path, body);
    },
    onSuccess: () => { reset(); setMessage("Gespeichert"); qc.invalidateQueries({ queryKey: [spec.queryKey] }); },
    onError: (error: Error) => setMessage(error.message),
  });
  const setActive = useMutation({
    mutationFn: (entry: Entry) => entry.active === false
      ? apiPut(`${spec.path}/${entry.id}`, { name: entry.name, description: entry.description ?? "", imageUrl: entry.imageUrl ?? "", sortOrder: entry.sortOrder ?? 0, active: true })
      : apiDelete(`${spec.path}/${entry.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: [spec.queryKey] }),
  });

  return <Card testID={`config-${spec.queryKey}`}>
    <SectionTitle>{spec.title}</SectionTitle>
    <Muted>{spec.help}</Muted>
    {(query.data ?? []).map((entry) => <View key={entry.id} style={styles.entryRow}>
      <View style={{ flex: 1 }}><Text style={styles.entryName}>{entry.name}</Text><Muted>{entry.active === false ? "Inaktiv" : "Aktiv"}{entry.description ? ` · ${entry.description}` : ""}</Muted></View>
      <Pressable testID={`edit-${entry.id}`} onPress={() => { setEditingId(entry.id); setName(entry.name); setDescription(entry.description ?? ""); setSortOrder(String(entry.sortOrder ?? 0)); }} style={styles.iconButton}><PencilSimple size={17} /></Pressable>
      <Button title={entry.active === false ? "Aktivieren" : "Deaktivieren"} kind="secondary" onPress={() => setActive.mutate(entry)} />
    </View>)}
    <View style={styles.form}>
      <Input value={name} onChangeText={setName} placeholder={editingId ? "Name bearbeiten" : "Neuer Eintrag"} />
      <Input value={description} onChangeText={setDescription} placeholder="Beschreibung (optional)" />
      <Input value={sortOrder} onChangeText={setSortOrder} keyboardType="number-pad" placeholder="Sortierung" />
      <View style={styles.actions}><Button title={editingId ? "Änderung speichern" : "Anlegen"} disabled={!name.trim()} loading={save.isPending} onPress={() => save.mutate()} style={{ flex: 1 }} />{editingId ? <Button title="Abbrechen" kind="secondary" onPress={reset} /> : null}</View>
      {message ? <Muted>{message}</Muted> : null}
    </View>
  </Card>;
}

export default function Einstellungen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const operations = useQuery<any[]>({ queryKey: ["commercial-operations"], queryFn: () => apiGet("/operations") });
  const paymentEvents = useQuery<any[]>({ queryKey: ["payment-events"], queryFn: () => apiGet("/payment-events") });
  const qc = useQueryClient();
  const retryPayment = useMutation({
    mutationFn: (id: string) => apiPost(`/payment-events/${id}/retry`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["payment-events"] });
      qc.invalidateQueries({ queryKey: ["commercial-operations"] });
      localizedAlert("Erneut verarbeitet", "Der Zahlungsstatus wurde sicher erneut geprüft.");
    },
    onError: (error: Error) => localizedAlert("Wiederholung nicht möglich", error.message),
  });
  const operationLabel = (value: string) => ({
    "order.create": "Bestellung und Rechnung", "offer.create": "Angebot erstellen",
    "offer.accept": "Angebot annehmen", "invoice.create_for_order": "Rechnung erstellen",
    "invoice.payment": "Zahlung erfassen", "invoice.checkout": "Rechnungszahlung starten",
    "customer_price.upsert": "Kundenpreis speichern", "shop_order.create": "Shop-Bestellung",
    "shop_order.checkout": "Shop-Zahlung starten", "subscription.create": "Abo anlegen",
    "subscriptions.run": "Abo-Bestellungen ausführen", "machine_request.create": "Maschinenanfrage",
    "machine_request.accept": "Maschinenangebot annehmen", "machine_request.checkout": "Maschinenzahlung starten",
    "equipment_request.create": "Finanzierungsanfrage",
  } as Record<string, string>)[value] ?? "Geschäftsvorgang";
  const stateLabel = (value: string) => ({ processing: "In Bearbeitung", failed_retryable: "Erneuter Versuch möglich", failed_terminal: "Abgelehnt" } as Record<string, string>)[value] ?? value;
  return <View style={styles.root}>
    <View style={styles.header}><Pressable onPress={() => router.back()} style={styles.back}><ArrowLeft size={20} color={colors.onSurface} /></Pressable><View><Text style={styles.title}>Stammdaten & Konfiguration</Text><Muted>Geschäftliche Strukturen ohne Programmierung verwalten</Muted></View></View>
    <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
      {SECTIONS.map((spec) => <ConfigSection key={spec.path} spec={spec} />)}
      <Card testID="commercial-operations">
        <SectionTitle>Offene Verarbeitungsvorgänge</SectionTitle>
        <Muted>Fehlgeschlagene oder noch laufende Bestell-, Rechnungs- und Zahlungsvorgänge.</Muted>
        {(operations.data ?? []).length === 0 ? <Muted>Keine offenen Vorgänge.</Muted> : (operations.data ?? []).map((entry) => <View key={entry.id} style={styles.operationRow}><View style={{ flex: 1 }}><Text style={styles.entryName}>{operationLabel(entry.operation)}</Text><Muted>{stateLabel(entry.status)} · Versuch {entry.attempts}</Muted></View><Text style={styles.operationCode}>{entry.status === "processing" ? "läuft" : "prüfen"}</Text></View>)}
      </Card>
      <Card testID="payment-events">
        <SectionTitle>Zahlungsverarbeitung</SectionTitle>
        <Muted>Fehlgeschlagene oder laufende Bestätigungen des Zahlungsdienstes.</Muted>
        {(paymentEvents.data ?? []).length === 0 ? <Muted>Keine offenen Zahlungsereignisse.</Muted> : (paymentEvents.data ?? []).map((entry) => <View key={entry.id} style={styles.operationRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.entryName}>{entry.resourceType === "invoice" ? "Rechnungszahlung" : entry.resourceType === "shop_order" ? "Shop-Zahlung" : entry.resourceType === "machine_request" ? "Maschinenzahlung" : "Zahlungsereignis"}</Text>
            <Muted>{stateLabel(entry.status)}{entry.resourceId ? ` · ${entry.resourceId}` : ""}{entry.lastErrorCode ? ` · ${entry.lastErrorCode}` : ""}</Muted>
          </View>
          {entry.status === "failed_retryable" ? <Button title="Erneut prüfen" kind="secondary" loading={retryPayment.isPending} onPress={() => localizedAlert("Zahlung erneut prüfen?", "ORDO verarbeitet ausschließlich das bereits verifizierte Ereignis erneut.", [{ text: "Abbrechen", style: "cancel" }, { text: "Erneut prüfen", onPress: () => retryPayment.mutate(entry.id) }])} /> : <Text style={styles.operationCode}>{entry.status === "processing" ? "läuft" : "prüfen"}</Text>}
        </View>)}
      </Card>
    </ScrollView>
  </View>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { padding: tokens.spacing.lg, paddingTop: tokens.spacing.xl, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider, flexDirection: "row", alignItems: "center", gap: 12 },
  back: { width: 42, height: 42, borderRadius: 10, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { color: c.onSurface, fontSize: 23, fontWeight: "800" },
  content: { width: "100%", maxWidth: 980, alignSelf: "center", padding: tokens.spacing.lg, gap: 16, paddingBottom: 48 },
  entryRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: c.divider },
  entryName: { color: c.onSurface, fontWeight: "800" },
  iconButton: { width: 40, height: 40, alignItems: "center", justifyContent: "center", borderRadius: 9, backgroundColor: c.surfaceTertiary },
  form: { gap: 9, marginTop: 14 },
  actions: { flexDirection: "row", gap: 8 },
  operationRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: c.divider },
  operationCode: { color: c.muted, fontSize: 12, fontWeight: "700" },
}));
