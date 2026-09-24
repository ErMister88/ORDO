import { useState } from "react";
import { View, Text, ScrollView, Pressable, Alert } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as WebBrowser from "expo-web-browser";
import { ArrowLeft, Phone, EnvelopeSimple, MapPin, Check, PencilSimple, Export, FileText, Package } from "phosphor-react-native";

import { makeStyles, tokens, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet, apiPost, apiPut } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { shareInvoicePdf, shareCollectivePdf } from "@/src/lib/pdf";
import { Card, InfoRow, Button, Input, StatusBadge, EmptyState, Muted, LoadingState, ErrorState, PageContainer, KPICard, SectionTitle } from "@/src/components/ui";

export default function KundeDetail() {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const qc = useQueryClient();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [tab, setTab] = useState<"uebersicht" | "aktivitaeten" | "konditionen" | "vorgaenge" | "rechnungen">("uebersicht");
  const [editing, setEditing] = useState(false);
  const isAdmin = user?.role === "admin";

  const company = useQuery({ queryKey: ["company", id], queryFn: () => apiGet(`/companies/${id}`) });
  const prices = useQuery({ queryKey: ["prices", id], queryFn: () => apiGet(`/companies/${id}/prices`) });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => apiGet("/orders") });
  const invoices = useQuery({ queryKey: ["invoices"], queryFn: () => apiGet("/invoices") });
  const offers = useQuery({ queryKey: ["offers"], queryFn: () => apiGet("/offers") });
  const contracts = useQuery({ queryKey: ["contracts"], queryFn: () => apiGet("/contracts") });
  const machineRequests = useQuery({ queryKey: ["machine-requests"], queryFn: () => apiGet("/machine-requests") });
  const activities = useQuery({ queryKey: ["customer-activities", id], queryFn: () => apiGet(`/companies/${id}/activities`) });
  const tasks = useQuery({ queryKey: ["customer-tasks", id], queryFn: () => apiGet(`/companies/${id}/tasks`) });
  const salesStaff = useQuery({ queryKey: ["sales-staff"], queryFn: () => apiGet("/staff/sales"), enabled: isAdmin });
  const history = useQuery({
    queryKey: ["price-history", id],
    queryFn: () => apiGet(`/companies/${id}/price-history`),
    enabled: isAdmin || user?.role === "sales",
  });

  const c = company.data;
  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));
  const custOrders = (orders.data ?? []).filter((o: any) => o.companyId === id);
  const custInvoices = (invoices.data ?? []).filter((i: any) => i.companyId === id);
  const custOffers = (offers.data ?? []).filter((o: any) => o.companyId === id);
  const custContracts = (contracts.data ?? []).filter((ct: any) => ct.companyId === id);
  const custMachines = (machineRequests.data ?? []).filter((request: any) => request.customer?.companyId === id);
  const pricingQuote = useQuery({
    queryKey: ["customer-360-pricing", id, (products.data ?? []).map((p: any) => p.id)],
    queryFn: () => apiPost("/pricing/b2b/quote", { companyId: id, items: (products.data ?? []).filter((p: any) => p.active !== false).map((p: any) => ({ productId: p.id, qty: 1 })) }),
    enabled: !!id && (products.data ?? []).length > 0,
  });
  const quoteMap: Record<string, any> = {};
  (pricingQuote.data?.lines ?? []).forEach((line: any) => { quoteMap[line.productId] = line; });

  const payInvoice = useMutation({
    mutationFn: (invId: string) => apiPut(`/invoices/${invId}/pay`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const [payingId, setPayingId] = useState<string | null>(null);
  const payOnline = async (invId: string) => {
    setPayingId(invId);
    try {
      const res = await apiPost(`/invoices/${invId}/checkout`, {});
      if (res?.url) {
        await WebBrowser.openBrowserAsync(res.url);
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 1500));
          const st = await apiGet(`/invoices/${invId}/payment-status`);
          if (st.status === "Bezahlt") {
            qc.invalidateQueries({ queryKey: ["invoices"] });
            qc.invalidateQueries({ queryKey: ["dashboard"] });
            break;
          }
        }
      }
    } catch (e: any) {
      Alert.alert("Zahlung", e.message || "Online-Zahlung ist erst nach dem Deploy verfügbar.");
    } finally {
      setPayingId(null);
    }
  };

  const [sammelBusy, setSammelBusy] = useState(false);
  const sammelrechnung = async () => {
    setSammelBusy(true);
    try {
      const now = new Date();
      const data = await apiGet(`/companies/${id}/collective-invoice?year=${now.getFullYear()}&month=${now.getMonth() + 1}`);
      await shareCollectivePdf(data);
    } catch (e: any) {
      Alert.alert("Sammelrechnung", e.message || "Fehler");
    } finally {
      setSammelBusy(false);
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title} numberOfLines={1}>
            {c?.name ?? "Kunde"}
          </Text>
          {c ? <Text style={styles.subtitle}>{c.city}</Text> : null}
        </View>
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <PageContainer narrow style={styles.page}>
        {company.isLoading ? <LoadingState label="Kundenprofil wird geladen…" /> : company.isError ? <ErrorState onRetry={() => company.refetch()} /> : null}
        {c && (
          <Card testID="company-info-card">
            <View style={styles.metaRow}>
              <MapPin size={15} color={colors.muted} />
              <Text style={styles.metaText}>{c.city}</Text>
            </View>
            <View style={styles.metaRow}>
              <EnvelopeSimple size={15} color={colors.muted} />
              <Text style={styles.metaText}>{c.email}</Text>
            </View>
            <View style={styles.metaRow}>
              <Phone size={15} color={colors.muted} />
              <Text style={styles.metaText}>{c.phone}</Text>
            </View>
            <View style={styles.divider} />
            <InfoRow label="USt-ID" value={c.vatId} />
            <InfoRow label="Monatsabsatz" value={`${num(c.monthlyKg)} kg`} />
            <InfoRow label="Bestellzyklus" value={`${num(c.orderCycleDays ?? 30)} Tage`} />
            <InfoRow label="CRM-Status" value={c.status ?? "Aktiv"} />
            <InfoRow label="Vertrieb" value={(salesStaff.data ?? []).find((row: any) => row.id === c.assignedSalesRepId)?.name ?? (c.assignedSalesRepId ? "Zugewiesen" : "Kein Vertriebler")} />
            {(isAdmin || user?.role === "sales") ? (
              <CustomerControls company={c} salesStaff={salesStaff.data ?? []} isAdmin={isAdmin} onSaved={() => { qc.invalidateQueries({ queryKey: ["company", id] }); qc.invalidateQueries({ queryKey: ["companies"] }); qc.invalidateQueries({ queryKey: ["customer-activities", id] }); }} />
            ) : null}
            {isAdmin && (
              <Pressable testID="edit-company" style={styles.editBtn} onPress={() => setEditing(true)}>
                <PencilSimple size={16} color={colors.brandPrimary} weight="bold" />
                <Text style={styles.editText}>Firma bearbeiten</Text>
              </Pressable>
            )}
          </Card>
        )}

        {isAdmin && editing && c && (
          <CompanyEditor
            company={c}
            onCancel={() => setEditing(false)}
            onSaved={() => {
              setEditing(false);
              qc.invalidateQueries({ queryKey: ["company", id] });
              qc.invalidateQueries({ queryKey: ["companies"] });
            }}
          />
        )}

        <View style={styles.segment}>
          {(["uebersicht", "aktivitaeten", "konditionen", "vorgaenge", "rechnungen"] as const).map((t) => (
            <Pressable
              key={t}
              testID={`segment-${t}`}
              style={[styles.segItem, tab === t && styles.segItemActive]}
              onPress={() => setTab(t)}
            >
              <Text style={[styles.segText, tab === t && styles.segTextActive]}>
                {t === "uebersicht" ? "Übersicht" : t === "aktivitaeten" ? "CRM" : t === "konditionen" ? "Preise" : t === "vorgaenge" ? "Vorgänge" : "Rechnungen"}
              </Text>
            </Pressable>
          ))}
        </View>

        {tab === "uebersicht" ? (
          <>
            <View style={styles.kpiGrid}>
              <KPICard label="Angebote" value={num(custOffers.length)} accent="warning" />
              <KPICard label="Bestellungen" value={num(custOrders.length)} />
              <KPICard label="Rechnungen" value={num(custInvoices.length)} accent="success" />
              <KPICard label="Verträge" value={num(custContracts.length)} accent="success" />
            </View>
            <SectionTitle>Verträge & Maschinen</SectionTitle>
            {custContracts.length === 0 && custMachines.length === 0 ? <EmptyState title="Keine Verträge oder Maschinen" /> : null}
            {custContracts.map((ct: any) => (
              <Card key={ct.id} testID={`customer-contract-${ct.id}`}>
                <View style={styles.orderTop}><View><Text style={styles.prodTitle}>{ct.machine || "Kaffeeliefervertrag"}</Text><Muted>{ct.id}</Muted></View><FileText size={19} color={colors.brandPrimary} /></View>
                <InfoRow label="Mindestabnahme" value={`${num(ct.minQtyMonth)} kg/Monat`} />
                <InfoRow label="Laufzeit" value={`${ct.termMonths} Monate`} />
                <InfoRow label="Vertragspreis" value={`${euro(ct.price)}/kg netto`} />
              </Card>
            ))}
            {custMachines.map((request: any) => (
              <Card key={request.id} testID={`customer-machine-${request.id}`}>
                <View style={styles.orderTop}><View><Text style={styles.prodTitle}>{request.machineName}</Text><Muted>{request.id} · {request.type}</Muted></View><StatusBadge status={request.status} /></View>
                {request.expectedCoffeeKgMonth ? <InfoRow label="Geplante Kaffeeabnahme" value={`${num(request.expectedCoffeeKgMonth)} kg/Monat`} /> : null}
              </Card>
            ))}
            <SectionTitle>Letzte Kundenaktivität</SectionTitle>
            {[...custOrders.map((row: any) => ({ ...row, kind: "Bestellung" })), ...custOffers.map((row: any) => ({ ...row, kind: "Angebot" }))]
              .sort((a: any, b: any) => String(b.createdAt).localeCompare(String(a.createdAt))).slice(0, 5).map((row: any) => (
                <Pressable key={`${row.kind}-${row.id}`} onPress={() => row.kind === "Bestellung" ? router.push(`/bestellung/${row.id}`) : setTab("vorgaenge")}>
                  <Card style={styles.activityCard}><View style={styles.activityKind}>{row.kind === "Bestellung" ? <Package size={16} color={colors.brandPrimary} /> : <FileText size={16} color={colors.brandPrimary} />}</View><View style={{ flex: 1 }}><Text style={styles.prodTitle}>{row.id}</Text><Muted>{dateDE(row.createdAt)}</Muted></View><StatusBadge status={row.status} /></Card>
                </Pressable>
              ))}
          </>
        ) : tab === "aktivitaeten" ? (
          <CustomerCRM companyId={id!} activities={activities.data ?? []} tasks={tasks.data ?? []} />
        ) : tab === "konditionen" ? (
          <>
            {isAdmin || user?.role === "sales" ? (
              <AdminConditions
                companyId={id!}
                products={products.data ?? []}
                prices={prices.data ?? []}
                quoteMap={quoteMap}
              />
            ) : pricingQuote.isLoading ? (
              <LoadingState label="Verbindliche Preise werden ermittelt…" />
            ) : pricingQuote.isError ? (
              <ErrorState message="Die Preise sind aktuell nicht verfügbar." onRetry={() => pricingQuote.refetch()} />
            ) : (
              (pricingQuote.data?.lines ?? []).map((quote: any) => {
                const p = prodMap[quote.productId];
                return (
                  <Card key={quote.productId} testID={`price-${quote.productId}`}>
                    <Text style={styles.prodTitle}>
                      {p ? `${p.brand} ${p.name}` : quote.productId}
                    </Text>
                    <InfoRow label={quote.basePriceSource === "customer_price" ? "Individueller Kundenpreis" : "B2B-Standardpreis"} value={`${euro(quote.finalUnitPrice)}/${p?.unit ?? "kg"} netto`} />
                    {quote.promotion ? <Muted>Aktion „{quote.promotion.name}“ bis {dateDE(quote.promotion.endsAt)}</Muted> : null}
                  </Card>
                );
              })
            )}
            {(isAdmin || user?.role === "sales") ? <>
              <Text style={styles.histTitle}>Preisänderungen</Text>
              {(history.data ?? []).length === 0 ? (
                <Muted>Noch keine Preisänderungen erfasst.</Muted>
              ) : (
                (history.data ?? []).map((h: any, idx: number) => {
                  const p = prodMap[h.productId];
                  return (
                    <Card key={idx} testID={`history-${idx}`}>
                      <Text style={styles.prodTitle}>{p ? `${p.brand} ${p.name}` : h.productId}</Text>
                      <Muted>
                        {h.oldPrice != null ? `${euro(h.oldPrice)} → ` : "Neu: "}
                        {euro(h.newPrice)}
                      </Muted>
                      <Muted>
                        {dateDE(h.changedAt)} · {h.changedByName}
                      </Muted>
                    </Card>
                  );
                })
              )}
            </> : null}
          </>
        ) : tab === "rechnungen" ? (
          <>
            {(isAdmin || user?.role === "sales") && (
              <Pressable testID="sammelrechnung" style={styles.sammelBtn} onPress={sammelrechnung} disabled={sammelBusy}>
                <Export size={16} color={colors.brandPrimary} weight="bold" />
                <Text style={styles.editText}>{sammelBusy ? "Erstelle…" : "Sammelrechnung (Monat) als PDF"}</Text>
              </Pressable>
            )}
            {custInvoices.length === 0 ? (
              <EmptyState title="Keine Rechnungen" subtitle="Für diesen Kunden liegen keine Rechnungen vor" />
            ) : (
              custInvoices.map((inv: any) => (
                <Card key={inv.id} testID={`invoice-${inv.id}`}>
                  <View style={styles.orderTop}>
                    <Text style={styles.prodTitle}>{inv.id}</Text>
                    <StatusBadge status={inv.status} />
                  </View>
                  <InfoRow label="Datum" value={dateDE(inv.date)} />
                  {inv.net != null ? <InfoRow label="Netto" value={euro(inv.net)} /> : null}
                  {inv.taxTotal != null ? <InfoRow label="MwSt" value={euro(inv.taxTotal)} /> : null}
                  <InfoRow label={inv.net != null ? "Brutto" : "Betrag"} value={euro(inv.amount)} />
                  <Pressable testID={`invoice-pdf-${inv.id}`} style={styles.sammelBtn} onPress={() => shareInvoicePdf(inv, c)}>
                    <Export size={16} color={colors.brandPrimary} weight="bold" />
                    <Text style={styles.editText}>Rechnung als PDF</Text>
                  </Pressable>
                  {inv.status !== "Bezahlt" ? (
                    <>
                      <Button
                        testID={`pay-online-${inv.id}`}
                        title="Online bezahlen (Karte)"
                        loading={payingId === inv.id}
                        onPress={() => payOnline(inv.id)}
                        style={{ marginTop: 8 }}
                      />
                      {(isAdmin || user?.role === "sales") ? <Button
                          testID={`pay-invoice-${inv.id}`}
                          title="Als bezahlt markieren"
                          kind="secondary"
                          loading={payInvoice.isPending && payInvoice.variables === inv.id}
                          onPress={() => payInvoice.mutate(inv.id)}
                          style={{ marginTop: 8 }}
                        /> : null}
                    </>
                  ) : null}
                </Card>
              ))
            )}
          </>
        ) : custOrders.length === 0 && custOffers.length === 0 ? (
          <EmptyState title="Keine Vorgänge" subtitle="Für diesen Kunden liegen keine Angebote oder Bestellungen vor" />
        ) : (
          <>
          {custOffers.map((offer: any) => (
            <Card key={offer.id} testID={`offer-${offer.id}`}>
              <View style={styles.orderTop}><View><Text style={styles.prodTitle}>{offer.id}</Text><Muted>Angebot · {dateDE(offer.createdAt)}</Muted></View><StatusBadge status={offer.status} /></View>
              <InfoRow label="Positionen" value={num(offer.items?.length ?? 0)} />
            </Card>
          ))}
          {custOrders.map((o: any) => {
            const total = o.netTotalMinor != null ? o.netTotalMinor / 100 : o.items.reduce((a: number, i: any) => a + i.price * i.qty, 0);
            return (
              <Pressable key={o.id} testID={`order-${o.id}`} onPress={() => router.push(`/bestellung/${o.id}`)}>
                <Card>
                  <View style={styles.orderTop}>
                    <Text style={styles.prodTitle}>{o.id}</Text>
                    <StatusBadge status={o.status} />
                  </View>
                  <InfoRow label="Datum" value={dateDE(o.createdAt)} />
                  <InfoRow label="Betrag" value={`${euro(total)} netto`} />
                </Card>
              </Pressable>
            );
          })}
          </>
        )}
        </PageContainer>
      </ScrollView>

      {(isAdmin || user?.role === "sales") ? <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>
        <View style={styles.footerActions}>
        <Button
          testID="create-order-cta"
          title="Neue Bestellung"
          kind="secondary"
          onPress={() => router.push({ pathname: "/(tabs)/bestellungen", params: { companyId: id } })}
          style={{ flex: 1 }}
        />
        <Button
          testID="create-offer-cta"
          title="Neues Angebot erstellen"
          onPress={() => router.push({ pathname: "/(tabs)/angebote", params: { companyId: id } })}
          style={{ flex: 1 }}
        />
        </View>
      </View> : null}
    </View>
  );
}

function AdminConditions({
  companyId,
  products,
  prices,
  quoteMap,
}: {
  companyId: string;
  products: any[];
  prices: any[];
  quoteMap: Record<string, any>;
}) {
  const qc = useQueryClient();
  const priceMap: Record<string, number> = {};
  prices.forEach((cp: any) => (priceMap[cp.productId] = cp.price));

  return (
    <>
      <Muted>Kundenpreise festlegen. Leer = Standardpreis gilt.</Muted>
      {products.map((p: any) => (
        <PriceEditorRow
          key={p.id}
          product={p}
          companyId={companyId}
          initial={priceMap[p.id]}
          quote={quoteMap[p.id]}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["prices", companyId] });
            qc.invalidateQueries({ queryKey: ["price-history", companyId] });
          }}
        />
      ))}
    </>
  );
}

const CRM_STATUSES = ["Lead", "Interessent", "Neukunde", "Aktiv", "Inaktiv", "Gesperrt"];

function CustomerControls({ company, salesStaff, isAdmin, onSaved }: { company: any; salesStaff: any[]; isAdmin: boolean; onSaved: () => void }) {
  const styles = useStyles();
  const status = useMutation({ mutationFn: (value: string) => apiPut(`/companies/${company.id}/status`, { status: value }), onSuccess: onSaved });
  const assignment = useMutation({ mutationFn: (value: string | null) => apiPut(`/companies/${company.id}/assignment`, { assignedSalesRepId: value }), onSuccess: onSaved });
  return (
    <View style={styles.customerControls}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.controlChips}>
        {CRM_STATUSES.map((value) => <Pressable key={value} testID={`status-${value}`} onPress={() => status.mutate(value)} style={[styles.controlChip, company.status === value && styles.controlChipActive]}><Text style={[styles.controlChipText, company.status === value && styles.controlChipTextActive]}>{value}</Text></Pressable>)}
      </ScrollView>
      {isAdmin ? <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.controlChips}>
        <Pressable testID="assign-none" onPress={() => assignment.mutate(null)} style={[styles.controlChip, !company.assignedSalesRepId && styles.controlChipActive]}><Text style={[styles.controlChipText, !company.assignedSalesRepId && styles.controlChipTextActive]}>Kein Vertriebler</Text></Pressable>
        {salesStaff.map((sales) => <Pressable key={sales.id} testID={`assign-${sales.id}`} onPress={() => assignment.mutate(sales.id)} style={[styles.controlChip, company.assignedSalesRepId === sales.id && styles.controlChipActive]}><Text style={[styles.controlChipText, company.assignedSalesRepId === sales.id && styles.controlChipTextActive]}>{sales.name}</Text></Pressable>)}
      </ScrollView> : null}
    </View>
  );
}

function CustomerCRM({ companyId, activities, tasks }: { companyId: string; activities: any[]; tasks: any[] }) {
  const styles = useStyles();
  const qc = useQueryClient();
  const [activityTitle, setActivityTitle] = useState("");
  const [activityNote, setActivityNote] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDue, setTaskDue] = useState("");
  const addActivity = useMutation({
    mutationFn: () => apiPost(`/companies/${companyId}/activities`, { type: "note", title: activityTitle, note: activityNote, internal: true }),
    onSuccess: () => { setActivityTitle(""); setActivityNote(""); qc.invalidateQueries({ queryKey: ["customer-activities", companyId] }); },
  });
  const addTask = useMutation({
    mutationFn: () => apiPost(`/companies/${companyId}/tasks`, { title: taskTitle, dueAt: new Date(`${taskDue}T12:00:00`).toISOString() }),
    onSuccess: () => { setTaskTitle(""); setTaskDue(""); qc.invalidateQueries({ queryKey: ["customer-tasks", companyId] }); },
  });
  const finishTask = useMutation({ mutationFn: (taskId: string) => apiPut(`/customer-tasks/${taskId}`, { status: "completed" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["customer-tasks", companyId] }) });
  return <>
    <Card><SectionTitle>Aktivität erfassen</SectionTitle><Input testID="activity-title" value={activityTitle} onChangeText={setActivityTitle} placeholder="z. B. Kundengespräch" /><Input testID="activity-note" value={activityNote} onChangeText={setActivityNote} placeholder="Interne Notiz" multiline /><Button title="Aktivität speichern" disabled={!activityTitle.trim()} loading={addActivity.isPending} onPress={() => addActivity.mutate()} /></Card>
    <Card><SectionTitle>Wiedervorlage</SectionTitle><Input testID="task-title" value={taskTitle} onChangeText={setTaskTitle} placeholder="Aufgabe" /><Input testID="task-due" value={taskDue} onChangeText={setTaskDue} placeholder="YYYY-MM-DD" /><Button title="Aufgabe anlegen" disabled={!taskTitle.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(taskDue)} loading={addTask.isPending} onPress={() => addTask.mutate()} /></Card>
    <SectionTitle>Offene Aufgaben</SectionTitle>
    {tasks.filter((task) => task.status === "open").length === 0 ? <EmptyState title="Keine offenen Aufgaben" /> : tasks.filter((task) => task.status === "open").map((task) => <Card key={task.id}><View style={styles.orderTop}><View style={{ flex: 1 }}><Text style={styles.prodTitle}>{task.title}</Text><Muted>Fällig {dateDE(task.dueAt)}</Muted></View><Button title="Erledigt" kind="secondary" onPress={() => finishTask.mutate(task.id)} /></View></Card>)}
    <SectionTitle>Aktivitäten</SectionTitle>
    {activities.length === 0 ? <EmptyState title="Noch keine Aktivitäten" /> : activities.map((activity) => <Card key={activity.id}><Text style={styles.prodTitle}>{activity.title}</Text><Muted>{dateDE(activity.occurredAt)} · {activity.createdByName || "System"}</Muted>{activity.note ? <Text style={styles.metaText}>{activity.note}</Text> : null}</Card>)}
  </>;
}

function PriceEditorRow({
  product,
  companyId,
  initial,
  quote,
  onSaved,
}: {
  product: any;
  companyId: string;
  initial?: number;
  quote?: any;
  onSaved: () => void;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const [val, setVal] = useState(initial != null ? String(initial) : "");
  const [saved, setSaved] = useState(false);
  const [feedback, setFeedback] = useState("");

  const save = useMutation({
    mutationFn: () =>
      apiPost("/customer-prices", {
        companyId,
        productId: product.id,
        price: Number((val || "").replace(",", ".")),
      }),
    onSuccess: (result: any) => {
      setSaved(!result.approvalRequired);
      setFeedback(result.approvalRequired ? result.message : "");
      onSaved();
      setTimeout(() => setSaved(false), 1500);
    },
  });

  return (
    <Card testID={`price-edit-${product.id}`}>
      <Text style={styles.prodTitle}>
        {product.brand} {product.name}
      </Text>
      <Muted>
        {quote?.basePriceSource === "customer_price" ? `Aktuell individuell ${euro(quote.finalUnitPrice)}` : `Aktuell B2B-Standard ${euro(quote?.finalUnitPrice ?? product.standardPrice)}`} netto
      </Muted>
      {quote?.promotion ? <Muted>Temporäre Aktion bis {dateDE(quote.promotion.endsAt)}</Muted> : null}
      <View style={styles.priceRow}>
        <Input
          testID={`price-input-${product.id}`}
          value={val}
          onChangeText={setVal}
          keyboardType="decimal-pad"
          placeholder={`${product.standardPrice}`}
          style={{ flex: 1 }}
        />
        <Pressable
          testID={`price-save-${product.id}`}
          disabled={!Number((val || "").replace(",", ".")) || save.isPending}
          onPress={() => save.mutate()}
          style={[styles.priceSave, save.isPending && { opacity: 0.5 }]}
        >
          <Check size={18} color={colors.onBrandPrimary} weight="bold" />
        </Pressable>
      </View>
      {saved ? <Text style={[styles.savedTxt, { color: colors.success }]}>Gespeichert</Text> : null}
      {feedback ? <Text style={[styles.savedTxt, { color: colors.warning }]}>{feedback}</Text> : null}
    </Card>
  );
}


function CompanyEditor({ company, onCancel, onSaved }: { company: any; onCancel: () => void; onSaved: () => void }) {
  const styles = useStyles();
  const [form, setForm] = useState({
    name: company.name ?? "",
    city: company.city ?? "",
    email: company.email ?? "",
    phone: company.phone ?? "",
    vatId: company.vatId ?? "",
    orderCycleDays: String(company.orderCycleDays ?? 30),
  });
  const set = (k: string) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const save = useMutation({
    mutationFn: () =>
      apiPut(`/companies/${company.id}`, {
        name: form.name,
        city: form.city,
        email: form.email,
        phone: form.phone,
        vatId: form.vatId,
        assignedSalesRepId: company.assignedSalesRepId ?? null,
        orderCycleDays: Number((form.orderCycleDays || "30").replace(",", ".")) || 30,
        active: company.active !== false,
      }),
    onSuccess: onSaved,
  });
  return (
    <Card testID="company-editor">
      <Text style={styles.prodTitle}>Firma bearbeiten</Text>
      <Text style={styles.editLabel}>Name</Text>
      <Input testID="edit-name" value={form.name} onChangeText={set("name")} />
      <Text style={styles.editLabel}>Ort</Text>
      <Input testID="edit-city" value={form.city} onChangeText={set("city")} />
      <Text style={styles.editLabel}>E-Mail</Text>
      <Input testID="edit-email" value={form.email} onChangeText={set("email")} autoCapitalize="none" keyboardType="email-address" />
      <Text style={styles.editLabel}>Telefon</Text>
      <Input testID="edit-phone" value={form.phone} onChangeText={set("phone")} keyboardType="phone-pad" />
      <Text style={styles.editLabel}>USt-ID</Text>
      <Input testID="edit-vat" value={form.vatId} onChangeText={set("vatId")} autoCapitalize="characters" />
      <Text style={styles.editLabel}>Bestellzyklus (Tage)</Text>
      <Input testID="edit-cycle" value={form.orderCycleDays} onChangeText={set("orderCycleDays")} keyboardType="numeric" />
      <Button testID="edit-save" title="Speichern" loading={save.isPending} onPress={() => save.mutate()} style={{ marginTop: 10 }} />
      <Button testID="edit-cancel" title="Abbrechen" kind="secondary" onPress={onCancel} style={{ marginTop: 8 }} />
    </Card>
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
  backBtn: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: c.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { fontSize: 24, fontWeight: "800", color: c.onSurface, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 2 },
  content: { padding: tokens.spacing.lg, paddingBottom: 24 },
  page: { gap: 12 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  metaText: { fontSize: 14, color: c.onSurfaceSecondary },
  divider: { height: 1, backgroundColor: c.divider, marginVertical: 6 },
  segment: {
    flexDirection: "row",
    backgroundColor: c.surfaceTertiary,
    borderRadius: 12,
    padding: 4,
    gap: 4,
  },
  kpiGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  activityCard: { flexDirection: "row", alignItems: "center" },
  activityKind: { width: 34, height: 34, borderRadius: 9, alignItems: "center", justifyContent: "center", backgroundColor: c.brandTertiary },
  segItem: { flex: 1, paddingVertical: 10, borderRadius: 9, alignItems: "center" },
  segItemActive: { backgroundColor: c.surface, shadowColor: c.onSurface, shadowOpacity: 0.06, shadowRadius: 4, elevation: 1 },
  segText: { fontSize: 14, fontWeight: "700", color: c.muted },
  segTextActive: { color: c.onSurface },
  prodTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  histTitle: { fontSize: 16, fontWeight: "800", color: c.onSurface, marginTop: 12, marginBottom: 2 },
  orderTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  priceRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 4 },
  priceSave: {
    width: 50,
    height: 50,
    borderRadius: 12,
    backgroundColor: c.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
  savedTxt: { fontSize: 13, fontWeight: "700", marginTop: 4 },
  editBtn: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10, paddingVertical: 8, justifyContent: "center", borderRadius: 10, backgroundColor: c.brandTertiary },
  editText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  editLabel: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  sammelBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 12, backgroundColor: c.brandTertiary, marginTop: 6 },
  footer: {
    padding: 20,
    paddingTop: 12,
    backgroundColor: c.surface,
    borderTopWidth: 1,
    borderTopColor: c.divider,
  },
  footerActions: { width: "100%", maxWidth: tokens.layout.narrow, alignSelf: "center", flexDirection: "row", gap: 10 },
  customerControls: { gap: 8, marginTop: 8 },
  controlChips: { gap: 6 },
  controlChip: { borderRadius: 999, backgroundColor: c.surfaceTertiary, borderWidth: 1, borderColor: c.border, paddingHorizontal: 11, paddingVertical: 7 },
  controlChipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  controlChipText: { fontSize: 12, fontWeight: "700", color: c.onSurfaceSecondary },
  controlChipTextActive: { color: c.onBrandPrimary },
}));
