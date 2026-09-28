import { useState } from "react";
import {
  Pressable,
  ScrollView,
  View,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ArrowLeft, ArrowRight } from "phosphor-react-native";

import { apiGet, apiPost, apiPostIdempotent, apiPut } from "@/src/api/client";
import { useAuth } from "@/src/auth/auth";
import { Button, Card, ErrorState, InfoRow, Input, KPICard, LoadingState, Muted, PageContainer, SectionTitle } from "@/src/components/ui";
import { dateDE, euro, num } from "@/src/lib/format";
import { makeStyles, tokens, useTheme } from "@/src/theme";
import { LocalizedText as Text, useI18n } from "@/src/i18n";

export default function SalesRepDetail() {
  const { tf } = useI18n();
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const qc = useQueryClient();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { id, period = "month" } = useLocalSearchParams<{ id: string; period?: string }>();
  const detail = useQuery({ queryKey: ["sales-rep-detail", id, period], queryFn: () => apiGet(`/dashboard/sales/${id}?period=${period}`) });
  const salesStaff = useQuery({ queryKey: ["sales-staff"], queryFn: () => apiGet("/staff/sales") });
  const agreements = useQuery({ queryKey: ["commission-agreements", id], queryFn: () => apiGet("/commission/agreements"), enabled: isAdmin });
  const ledger = useQuery({ queryKey: ["commission-ledger", id], queryFn: () => apiGet(`/commission/ledger?sales_rep_id=${id}`) });
  const settlements = useQuery({ queryKey: ["commission-settlements", id], queryFn: () => apiGet("/commission/settlements") });
  const [rateMinor, setRateMinor] = useState("");
  const [currency, setCurrency] = useState<"EUR" | "CHF">("EUR");
  const [companyId, setCompanyId] = useState("");
  const [productId, setProductId] = useState("");
  const [payoutReference, setPayoutReference] = useState("");
  const refreshCommission = () => {
    qc.invalidateQueries({ queryKey: ["commission-agreements", id] });
    qc.invalidateQueries({ queryKey: ["commission-ledger", id] });
    qc.invalidateQueries({ queryKey: ["commission-settlements", id] });
  };
  const createAgreement = useMutation({
    mutationFn: () => apiPost("/commission/agreements", {
      salesRepId: id, commissionType: "PER_KG", rateMinor: Number(rateMinor), currency,
      companyId: companyId.trim() || null, productId: productId.trim() || null, active: true,
    }),
    onSuccess: () => { setRateMinor(""); refreshCommission(); },
  });
  const createSettlement = useMutation({
    mutationFn: () => {
      const start = new Date(); start.setUTCDate(1); start.setUTCHours(0, 0, 0, 0);
      const end = new Date(start); end.setUTCMonth(end.getUTCMonth() + 1);
      return apiPostIdempotent("/commission/settlements", { salesRepId: id, currency, periodStart: start.toISOString(), periodEnd: end.toISOString() });
    },
    onSuccess: refreshCommission,
  });
  const deactivateAgreement = useMutation({
    mutationFn: (agreement: any) => apiPut(`/commission/agreements/${agreement.id}`, {
      salesRepId: agreement.salesRepId,
      commissionType: agreement.commissionType,
      rateMinor: agreement.rateMinor,
      currency: agreement.currency,
      companyId: agreement.companyId ?? null,
      productId: agreement.productId ?? null,
      active: false,
      validFrom: agreement.validFrom ?? null,
      validUntil: agreement.validUntil ?? null,
    }),
    onSuccess: refreshCommission,
  });
  const payout = useMutation({
    mutationFn: (settlementId: string) => apiPost(`/commission/settlements/${settlementId}/payout`, { reference: payoutReference }),
    onSuccess: () => { setPayoutReference(""); refreshCommission(); },
  });
  return <View style={styles.root}>
    <View style={styles.header}><Pressable onPress={() => router.back()} style={styles.back}><ArrowLeft size={20} color={colors.onSurface} /></Pressable><View><Text style={styles.title}>Vertriebsanalyse</Text><Text style={styles.subtitle}>Zeitraum wie im Dashboard: {period === "month" ? "Monat" : period === "quarter" ? "Quartal" : "Jahr"}</Text></View></View>
    <ScrollView contentContainerStyle={styles.content}><PageContainer>
      {detail.isLoading ? <LoadingState label="Vertriebsdaten werden geladen…" /> : detail.isError ? <ErrorState onRetry={() => detail.refetch()} /> : <>
        <SectionTitle>{(salesStaff.data ?? []).find((row: any) => row.id === id)?.name || detail.data.salesRep.name}</SectionTitle>
        <View style={styles.kpis}><KPICard label="Umsatz" value={euro(detail.data.salesRep.revenue)} /><KPICard label="Bestellungen" value={num(detail.data.salesRep.orders)} /><KPICard label="Kunden" value={num(detail.data.salesRep.activeCustomers)} /><KPICard label="Neukunden" value={num(detail.data.salesRep.newCustomers)} /><KPICard label="Menge" value={num(detail.data.salesRep.quantity)} />{detail.data.salesRep.marginDataComplete ? <KPICard label="Deckungsbeitrag" value={euro(detail.data.salesRep.margin)} accent="success" /> : null}</View>
        {!detail.data.salesRep.marginDataComplete ? <Muted>Deckungsbeitrag wird nicht ausgewiesen, weil historische Kostendaten nicht vollständig sind.</Muted> : null}
        <SectionTitle>Kunden im Zeitraum</SectionTitle>
        {detail.data.customers.map((customer: any) => <Pressable key={customer.companyId} onPress={() => router.push({ pathname: "/kunde/[id]", params: { id: customer.companyId, period } })}><Card><View style={styles.customerTop}><View style={{ flex: 1 }}><Text style={styles.customerName}>{customer.name}</Text><Muted>{customer.lastOrderAt ? `Letzter Auftrag ${dateDE(customer.lastOrderAt)}` : "Kein Auftrag im Zeitraum"}</Muted></View><ArrowRight size={18} color={colors.muted} /></View><InfoRow label="Umsatz" value={euro(customer.revenue)} /><InfoRow label="Bestellungen" value={num(customer.orders)} /><InfoRow label="Menge" value={num(customer.quantity)} />{customer.marginDataComplete ? <InfoRow label="Deckungsbeitrag" value={euro(customer.margin)} /> : null}</Card></Pressable>)}
        <SectionTitle>Provisions-Ledger</SectionTitle>
        {(ledger.data ?? []).length === 0 ? <Card><Muted>Noch keine Provisionsbewegungen.</Muted></Card> : (ledger.data ?? []).slice(0, 100).map((row: any) => <Card key={row.id}><InfoRow label={row.status} value={`${((row.amountMinor ?? 0) / 100).toFixed(2)} ${row.currency}`} /><Muted>{row.orderId ?? row.adjustmentReference ?? ""}</Muted></Card>)}
        <SectionTitle>Provisionsabrechnungen</SectionTitle>
        {(settlements.data ?? []).filter((row: any) => row.salesRepId === id).map((row: any) => <Card key={row.id}><InfoRow label={row.status} value={`${((row.amountMinor ?? 0) / 100).toFixed(2)} ${row.currency}`} />{isAdmin && row.status === "LOCKED" ? <><Input value={payoutReference} onChangeText={setPayoutReference} placeholder="Auszahlungsreferenz" /><Button title="Als ausgezahlt markieren" disabled={!payoutReference.trim()} loading={payout.isPending} onPress={() => payout.mutate(row.id)} /></> : null}</Card>)}
        {isAdmin ? <>
          <SectionTitle>Provisionsvereinbarung</SectionTitle>
          <Card><Muted>PER_KG wird nur auf unveränderliche kg-Positionssnapshots angewendet.</Muted><View style={styles.formRow}><Input value={rateMinor} onChangeText={setRateMinor} keyboardType="number-pad" placeholder="Rate in Cent je kg" style={{ flex: 1 }} /><View style={styles.currencyRow}>{(["EUR", "CHF"] as const).map((code) => <Pressable key={code} onPress={() => setCurrency(code)} style={[styles.currencyChip, currency === code && styles.currencyChipActive]}><Text style={[styles.currencyText, currency === code && styles.currencyTextActive]}>{code}</Text></Pressable>)}</View></View><Input value={companyId} onChangeText={setCompanyId} placeholder="Kunden-ID (optional)" /><Input value={productId} onChangeText={setProductId} placeholder="Produkt-ID (optional)" /><Button title="Vereinbarung anlegen" disabled={!rateMinor || Number(rateMinor) <= 0} loading={createAgreement.isPending} onPress={() => createAgreement.mutate()} /></Card>
          <SectionTitle>Monatsabrechnung</SectionTitle>
          <Card><Muted>Erstellt eine gesperrte Abrechnung für den aktuellen UTC-Kalendermonat.</Muted><Button title="Abrechnung erstellen" loading={createSettlement.isPending} onPress={() => createSettlement.mutate()} /></Card>
          {(agreements.data ?? []).filter((row: any) => row.salesRepId === id).map((row: any) => <Card key={row.id}><InfoRow label="PER_KG" value={`${(row.rateMinor / 100).toFixed(2)} ${row.currency}/kg`} /><Muted>{row.companyId ? tf("Kunde {id}", { id: row.companyId }) : "Alle Kunden"} · {row.productId ? tf("Produkt {id}", { id: row.productId }) : "Alle Produkte"}</Muted><InfoRow label="Status" value={row.active ? "Aktiv" : "Inaktiv"} />{row.active ? <Button title="Vereinbarung deaktivieren" kind="secondary" loading={deactivateAgreement.isPending} onPress={() => deactivateAgreement.mutate(row)} /> : null}</Card>)}
        </> : null}
      </>}
    </PageContainer></ScrollView>
  </View>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 18, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  back: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 21, fontWeight: "900", color: c.onSurface },
  subtitle: { fontSize: 13, color: c.muted },
  content: { padding: tokens.spacing.lg, paddingBottom: 36 },
  kpis: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  customerTop: { flexDirection: "row", alignItems: "center", gap: 10 },
  customerName: { fontSize: 16, fontWeight: "900", color: c.onSurface },
  formRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, alignItems: "center" },
  currencyRow: { flexDirection: "row", gap: 6 },
  currencyChip: { paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  currencyChipActive: { backgroundColor: c.brandPrimary },
  currencyText: { color: c.onSurfaceSecondary, fontWeight: "800" },
  currencyTextActive: { color: c.onBrandPrimary },
}));
