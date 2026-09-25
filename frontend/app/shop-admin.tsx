import { useEffect, useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform, Switch, Alert } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiDelete, apiGet, apiPut, apiPost } from "@/src/api/client";
import { euro, dateDE } from "@/src/lib/format";
import { Card, Input, Button, SectionTitle, StatusBadge, EmptyState, Muted, InfoRow } from "@/src/components/ui";

export default function ShopAdmin() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const settings = useQuery({ queryKey: ["shop-settings"], queryFn: () => apiGet("/shop/settings") });
  const orders = useQuery({ queryKey: ["shop-orders"], queryFn: () => apiGet("/shop/orders") });
  const pushStats = useQuery({ queryKey: ["push-stats"], queryFn: () => apiGet("/push/stats") });
  const collections = useQuery({ queryKey: ["shop-collections-admin"], queryFn: () => apiGet("/shop-collections") });
  const [collectionName, setCollectionName] = useState("");
  const [collectionDescription, setCollectionDescription] = useState("");
  const createCollection = useMutation({ mutationFn: () => apiPost("/shop-collections", { name: collectionName, description: collectionDescription, imageUrl: "", sortOrder: (collections.data?.length ?? 0) * 10, active: true }), onSuccess: () => { setCollectionName(""); setCollectionDescription(""); qc.invalidateQueries({ queryKey: ["shop-collections-admin"] }); } });

  const [thr, setThr] = useState("");
  const [fee, setFee] = useState("");
  const [nlPercent, setNlPercent] = useState("10");
  const [nlEnabled, setNlEnabled] = useState(true);
  const [subPercent, setSubPercent] = useState("");
  useEffect(() => {
    if (settings.data) {
      setThr(String(settings.data.freeShippingThreshold));
      setFee(String(settings.data.shippingFee));
      setNlPercent(String(settings.data.newsletterDiscountPercent ?? 10));
      setNlEnabled(settings.data.newsletterDiscountEnabled ?? true);
      setSubPercent(settings.data.subscriptionDiscountPercent == null ? "" : String(settings.data.subscriptionDiscountPercent));
    }
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () =>
      apiPut("/shop/settings", {
        freeShippingThreshold: Number(thr.replace(",", ".")) || 0,
        shippingFee: Number(fee.replace(",", ".")) || 0,
        newsletterDiscountPercent: Math.max(0, Math.min(100, Math.round(Number(nlPercent.replace(",", ".")) || 0))),
        newsletterDiscountEnabled: nlEnabled,
        subscriptionDiscountPercent: subPercent.trim() === "" ? null : Math.max(0, Math.min(100, Math.round(Number(subPercent.replace(",", "."))))),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shop-settings"] }),
  });

  const [pushTitle, setPushTitle] = useState("");
  const [pushMsg, setPushMsg] = useState("");
  const broadcast = useMutation({
    mutationFn: () => apiPost("/push/broadcast", { title: pushTitle, message: pushMsg, actionUrl: "/shop" }),
    onSuccess: (res: any) => {
      setPushTitle("");
      setPushMsg("");
      Alert.alert("Push gesendet", `An ${res.recipients} Gerät(e) gesendet.`);
    },
    onError: (e: any) => Alert.alert("Hinweis", e.message || "Push konnte nicht gesendet werden."),
  });

  const SHOP_STATUSES = ["Neu", "Bestätigt", "In Bearbeitung", "Versendet", "Abgeschlossen"];
  const [tracking, setTracking] = useState<Record<string, string>>({});
  const updateStatus = useMutation({
    mutationFn: ({ id, status, trackingNumber }: { id: string; status: string; trackingNumber?: string }) =>
      apiPut(`/shop/orders/${id}/status`, { status, trackingNumber }),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["shop-orders"] });
      Alert.alert("Status aktualisiert", `Bestellung auf „${vars.status}" gesetzt. Der Kunde wurde per E-Mail informiert.`);
    },
    onError: (e: any) => Alert.alert("Fehler", e.message || "Status konnte nicht geändert werden."),
  });

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} style={styles.iconBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Shop-Verwaltung</Text>
          <Text style={styles.subtitle}>Versand & B2C-Bestellungen</Text>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          <SectionTitle>Versandeinstellungen</SectionTitle>
          <Card>
            <Text style={styles.label}>Gratis-Versand ab (€)</Text>
            <Input testID="shop-threshold" value={thr} onChangeText={setThr} keyboardType="decimal-pad" />
            <Text style={styles.label}>Versandkosten sonst (€)</Text>
            <Input testID="shop-fee" value={fee} onChangeText={setFee} keyboardType="decimal-pad" />
            <Button testID="shop-settings-save" title="Speichern" loading={save.isPending} onPress={() => save.mutate()} style={{ marginTop: 10 }} />
          </Card>

          <SectionTitle style={{ marginTop: 4 }}>B2C-Shop-Collections</SectionTitle>
          <Card>
            <Muted>Eigene Shopbereiche unabhängig von Marke und Produktkategorie verwalten.</Muted>
            <Input value={collectionName} onChangeText={setCollectionName} placeholder="Name der Collection" />
            <Input value={collectionDescription} onChangeText={setCollectionDescription} placeholder="Beschreibung (optional)" />
            <Button title="Collection anlegen" disabled={!collectionName.trim()} loading={createCollection.isPending} onPress={() => createCollection.mutate()} />
          </Card>
          {(collections.data ?? []).map((collection: any) => <CollectionRow key={collection.id} collection={collection} onSaved={() => qc.invalidateQueries({ queryKey: ["shop-collections-admin"] })} />)}

          <SectionTitle style={{ marginTop: 4 }}>Monats-Abo</SectionTitle>
          <Card>
            <Text style={styles.label}>Abo-Rabatt in Prozent (%)</Text>
            <Muted>Leer lassen, solange kein B2C-Abo-Rabatt freigegeben ist.</Muted>
            <Input testID="subscription-percent" value={subPercent} onChangeText={setSubPercent} keyboardType="number-pad" />
            <Button title="Abo-Rabatt speichern" loading={save.isPending} onPress={() => save.mutate()} style={{ marginTop: 10 }} />
          </Card>

          <SectionTitle style={{ marginTop: 4 }}>Newsletter-Rabatt</SectionTitle>
          <Card>
            <View style={styles.switchRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.label2}>Rabatt aktiv</Text>
                <Muted>Abonnenten erhalten einen Rabattcode per E-Mail</Muted>
              </View>
              <Switch
                testID="nl-enabled"
                value={nlEnabled}
                onValueChange={setNlEnabled}
                trackColor={{ true: colors.brandPrimary, false: colors.divider }}
              />
            </View>
            <Text style={styles.label}>Rabatt in Prozent (%)</Text>
            <Input testID="nl-percent" value={nlPercent} onChangeText={setNlPercent} keyboardType="number-pad" />
            <Button testID="nl-save" title="Newsletter-Rabatt speichern" loading={save.isPending} onPress={() => save.mutate()} style={{ marginTop: 10 }} />
          </Card>

          <SectionTitle style={{ marginTop: 4 }}>Push-Nachricht an Kunden</SectionTitle>
          <Card>
            <Muted>
              {pushStats.data?.registered ?? 0} Gerät(e) registriert · funktioniert erst nach Veröffentlichung + App-Build.
            </Muted>
            <Text style={styles.label}>Titel</Text>
            <Input testID="push-title" value={pushTitle} onChangeText={setPushTitle} placeholder="z. B. Nur heute: 20% auf Espresso" />
            <Text style={styles.label}>Nachricht</Text>
            <Input testID="push-message" value={pushMsg} onChangeText={setPushMsg} placeholder="Befristetes Angebot – jetzt zugreifen!" multiline style={{ minHeight: 70 }} />
            <Button
              testID="push-send"
              title="An alle Kunden senden"
              loading={broadcast.isPending}
              onPress={() => {
                if (!pushTitle.trim() || !pushMsg.trim()) {
                  Alert.alert("Angaben fehlen", "Bitte Titel und Nachricht ausfüllen.");
                  return;
                }
                broadcast.mutate();
              }}
              style={{ marginTop: 10 }}
            />
          </Card>

          <SectionTitle style={{ marginTop: 4 }}>Shop-Bestellungen ({orders.data?.length ?? 0})</SectionTitle>
          {(orders.data ?? []).length === 0 ? (
            <EmptyState title="Noch keine Shop-Bestellungen" subtitle="B2C-Bestellungen erscheinen hier" />
          ) : (
            (orders.data ?? []).map((o: any) => (
              <Card key={o.id} testID={`shop-order-${o.id}`}>
                <View style={styles.rowTop}>
                  <Text style={styles.oId}>{o.id}</Text>
                  <StatusBadge status={o.paymentStatus} />
                </View>
                <Muted>
                  {dateDE(o.createdAt)} · {o.customer?.name} · {o.items.length} Artikel
                </Muted>
                <InfoRow label="Versand" value={o.shipping === 0 ? "Gratis" : euro(o.shipping)} />
                {o.taxTotal != null ? <InfoRow label="enthaltene MwSt" value={euro(o.taxTotal)} /> : null}
                <InfoRow label="Gesamt" value={euro(o.total)} />
                {o.customer?.street ? (
                  <Muted>
                    {o.customer.street}, {o.customer.zip} {o.customer.city}
                  </Muted>
                ) : null}
                <Text style={styles.statusLabel}>GLS-Sendungsnummer (optional)</Text>
                <Input
                  testID={`tracking-${o.id}`}
                  value={tracking[o.id] ?? o.trackingNumber ?? ""}
                  onChangeText={(v) => setTracking((t) => ({ ...t, [o.id]: v }))}
                  placeholder="z. B. 01234567890"
                  autoCapitalize="characters"
                />
                <Text style={styles.statusLabel}>Lieferstatus</Text>
                <View style={styles.chips}>
                  {SHOP_STATUSES.map((st) => {
                    const active = (o.status || "Neu") === st;
                    return (
                      <Pressable
                        key={st}
                        testID={`status-${o.id}-${st}`}
                        disabled={active || updateStatus.isPending}
                        onPress={() => updateStatus.mutate({ id: o.id, status: st, trackingNumber: (tracking[o.id] ?? o.trackingNumber) || undefined })}
                        style={[styles.chip, active && styles.chipActive]}
                      >
                        <Text style={[styles.chipTxt, active && styles.chipTxtActive]}>{st}</Text>
                      </Pressable>
                    );
                  })}
                </View>
              </Card>
            ))
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function CollectionRow({ collection, onSaved }: { collection: any; onSaved: () => void }) {
  const styles = useStyles();
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description ?? "");
  const [sortOrder, setSortOrder] = useState(String(collection.sortOrder ?? 0));
  const save = useMutation({ mutationFn: (active: boolean) => apiPut(`/shop-collections/${collection.id}`, { name, description, imageUrl: collection.imageUrl ?? "", sortOrder: Number(sortOrder) || 0, active }), onSuccess: onSaved });
  const archive = useMutation({ mutationFn: () => apiDelete(`/shop-collections/${collection.id}`), onSuccess: onSaved });
  return <Card testID={`collection-${collection.id}`}>
    <Input value={name} onChangeText={setName} placeholder="Name" />
    <Input value={description} onChangeText={setDescription} placeholder="Beschreibung" />
    <Input value={sortOrder} onChangeText={setSortOrder} placeholder="Sortierung" keyboardType="number-pad" />
    <View style={styles.collectionActions}><Button title="Speichern" kind="secondary" loading={save.isPending} onPress={() => save.mutate(collection.active !== false)} style={{ flex: 1 }} /><Button title={collection.active === false ? "Aktivieren" : "Deaktivieren"} kind="secondary" onPress={() => save.mutate(collection.active === false)} style={{ flex: 1 }} /><Button title="Archivieren" kind="secondary" loading={archive.isPending} onPress={() => archive.mutate()} style={{ flex: 1 }} /></View>
  </Card>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingBottom: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  subtitle: { fontSize: 13, color: c.muted, marginTop: 1 },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  label2: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  switchRow: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 4 },
  rowTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 4 },
  oId: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  statusLabel: { fontSize: 12, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 10, marginBottom: 6 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  collectionActions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingVertical: 6, paddingHorizontal: 10, borderRadius: 999, backgroundColor: c.surfaceTertiary, borderWidth: 1, borderColor: c.border },
  chipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  chipTxt: { fontSize: 12, fontWeight: "700", color: c.onSurfaceSecondary },
  chipTxtActive: { color: c.onBrandPrimary },
}));
