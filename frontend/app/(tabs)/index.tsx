import { useCallback, useState } from "react";
import { View, Text, ScrollView, RefreshControl, Pressable, useWindowDimensions } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { SignOut, Warning, ArrowRight, Coffee, Package, UsersThree, FileText, Receipt, Storefront } from "phosphor-react-native";

import { makeStyles, tokens, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet } from "@/src/api/client";
import { euro, num } from "@/src/lib/format";
import { ScreenHeader, HeaderButton } from "@/src/components/screen-header";
import { Card, KPICard, SectionTitle, InfoRow, Muted, PageContainer, LoadingState, ErrorState, StatusBadge } from "@/src/components/ui";

export default function Dashboard() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [period, setPeriod] = useState("month");

  const { data, isLoading, isError, refetch, isRefetching } = useQuery({
    queryKey: ["dashboard", period],
    queryFn: () => apiGet(`/dashboard?period=${period}`),
  });

  const onSignOut = useCallback(async () => {
    await signOut();
    router.replace("/login");
  }, []);

  const roleLabel =
    user?.role === "admin" ? "Administrator" : user?.role === "sales" ? "Vertrieb" : "Kundenportal";

  return (
    <View style={styles.root}>
      <ScreenHeader
        title={user?.role === "customer" ? "Willkommen" : "Übersicht"}
        subtitle={`${user?.name} · ${roleLabel}`}
        right={
          <View style={styles.headerBtns}>
            {user?.role === "admin" && (
              <HeaderButton onPress={() => router.push("/produkte")} testID="products-button">
                <Package size={20} color={colors.onSurfaceSecondary} weight="bold" />
              </HeaderButton>
            )}
            {user?.role === "admin" && (
              <HeaderButton onPress={() => router.push("/benutzer")} testID="users-button">
                <UsersThree size={20} color={colors.onSurfaceSecondary} weight="bold" />
              </HeaderButton>
            )}
            <HeaderButton onPress={onSignOut} testID="logout-button">
              <SignOut size={20} color={colors.onSurfaceSecondary} weight="bold" />
            </HeaderButton>
          </View>
        }
      />

      <ScrollView
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.brandPrimary} />
        }
      >
        <PageContainer style={styles.page}>
        {isError ? (
          <ErrorState onRetry={refetch} />
        ) : isLoading || !data ? (
          <LoadingState label="Dashboard wird vorbereitet…" />
        ) : data.role === "customer" ? (
          <CustomerDash data={data} />
        ) : (
          <StaffDash
            data={data}
            period={period}
            onPeriod={setPeriod}
            onCustomer={(id: string) => router.push(`/kunde/${id}`)}
            onApprovals={() => router.push("/(tabs)/angebote")}
            onActivity={(row: any) => row.companyId ? router.push(`/kunde/${row.companyId}`) : undefined}
          />
        )}
        </PageContainer>
      </ScrollView>
    </View>
  );
}

function StaffDash({
  data,
  onCustomer,
  onApprovals,
  onActivity,
  period,
  onPeriod,
}: {
  data: any;
  onCustomer: (id: string) => void;
  onApprovals: () => void;
  onActivity: (row: any) => void;
  period: string;
  onPeriod: (period: string) => void;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const { width } = useWindowDimensions();
  const desktop = width >= tokens.layout.tablet;
  return (
    <>
      <View style={styles.periodRow}>{[["month", "Monat"], ["quarter", "Quartal"], ["year", "Jahr"]].map(([value, label]) => <Pressable key={value} onPress={() => onPeriod(value)} style={[styles.periodChip, period === value && styles.periodChipActive]}><Text style={[styles.periodText, period === value && styles.periodTextActive]}>{label}</Text></Pressable>)}</View>
      <View style={[styles.topGrid, desktop && styles.topGridDesktop]}>
        <View style={styles.hero} testID="hero-revenue">
          <View style={styles.heroIcon}>
            <Coffee size={22} color={colors.onBrand} weight="fill" />
          </View>
          <Text style={styles.heroLabel}>Umsatz diesen Monat</Text>
          <Text style={styles.heroValue}>{euro(data.revenueMonth)}</Text>
          <Text style={styles.heroSub}>{data.ordersCount} Bestellungen im sichtbaren Kundenbestand</Text>
        </View>
        <View style={styles.kpiArea}>
          <View style={styles.kpiGrid}>
            <KPICard testID="kpi-customers" label="Aktive B2B-Kunden" value={num(data.activeCustomers)} />
            <KPICard testID="kpi-offers" label="Offene Angebote" value={num(data.openOffers)} accent="warning" />
          </View>
          <View style={styles.kpiGrid}>
            <KPICard testID="kpi-contracts" label="Aktive Verträge" value={num(data.activeContracts)} accent="success" />
            <KPICard testID="kpi-invoices" label="Offene Rechnungen" value={euro(data.openInvoices)} accent="error" />
          </View>
        </View>
      </View>

      {data.pendingApprovals > 0 && (
        <Pressable onPress={onApprovals} testID="approval-alert">
          <View style={[styles.alert, { borderColor: colors.warning }]}>
            <View style={[styles.alertIcon, { backgroundColor: colors.warning }]}>
              <Warning size={18} color={colors.onWarning} weight="fill" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.alertTitle}>Preisfreigabe erforderlich</Text>
              <Text style={styles.alertSub}>
                {data.pendingApprovals} Angebot(e) warten auf Ihre Freigabe
              </Text>
            </View>
          </View>
        </Pressable>
      )}

      <View style={[styles.operationalGrid, desktop && styles.operationalGridDesktop]}>
        <Card style={styles.operationCard}>
          <SectionTitle>Operativer Überblick</SectionTitle>
          <View style={styles.operationRows}>
            <InfoRow label="Monatsmenge der Kunden" value={`${num(data.totalKg)} kg`} />
            <InfoRow label="Maschinen im Feld" value={num(data.machinesInField)} />
            {data.shopOrders != null ? <InfoRow label="B2C-Shop-Bestellungen" value={num(data.shopOrders)} /> : null}
          </View>
        </Card>
        <Card style={styles.operationCard}>
          <SectionTitle>Systembereiche</SectionTitle>
          <View style={styles.systemRow}><UsersThree size={17} color={colors.brandSecondary} /><Text style={styles.systemText}>Kunden & Vertrieb</Text></View>
          <View style={styles.systemRow}><FileText size={17} color={colors.brandSecondary} /><Text style={styles.systemText}>Angebote & Verträge</Text></View>
          <View style={styles.systemRow}><Receipt size={17} color={colors.brandSecondary} /><Text style={styles.systemText}>Bestellungen & Rechnungen</Text></View>
          {data.shopOrders != null ? <View style={styles.systemRow}><Storefront size={17} color={colors.brandSecondary} /><Text style={styles.systemText}>B2C Commerce</Text></View> : null}
        </Card>
      </View>

      {data.recentActivity?.length > 0 ? (
        <>
          <SectionTitle style={{ marginTop: 4 }}>Letzte Aktivitäten</SectionTitle>
          <Card style={{ padding: 0, overflow: "hidden" }}>
            {data.recentActivity.map((row: any, index: number) => (
              <Pressable key={`${row.type}-${row.id}`} onPress={() => onActivity(row)} style={[styles.activityRow, index > 0 && styles.activityBorder]}>
                <View style={styles.activityIcon}>{row.type === "order" ? <Package size={16} color={colors.brandPrimary} /> : row.type === "offer" ? <FileText size={16} color={colors.brandPrimary} /> : <Receipt size={16} color={colors.brandPrimary} />}</View>
                <View style={{ flex: 1 }}><Text style={styles.activityTitle}>{row.id}</Text><Text style={styles.activityMeta}>{row.companyName || "B2C-Shop"}</Text></View>
                <StatusBadge status={row.status} />
                <ArrowRight size={16} color={colors.muted} />
              </Pressable>
            ))}
          </Card>
        </>
      ) : null}

      {data.followups?.length > 0 && (
        <>
          <SectionTitle style={{ marginTop: 8 }}>Kunden nachfassen</SectionTitle>
          <Card style={{ padding: 6 }}>
            {data.followups.map((f: any, i: number) => (
              <Pressable
                key={f.companyId}
                onPress={() => onCustomer(f.companyId)}
                testID={`followup-${f.companyId}`}
                style={[styles.followRow, i > 0 && styles.followRowBorder]}
              >
                <View style={[styles.overdueDot, { backgroundColor: colors.error }]} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.followName}>{f.name}</Text>
                  <Text style={styles.followSub}>
                    {f.days == null ? "Noch keine Bestellung" : `${f.days} Tage seit letzter Bestellung`}
                  </Text>
                </View>
                <ArrowRight size={18} color={colors.muted} />
              </Pressable>
            ))}
          </Card>
        </>
      )}
      {data.topSalesReps ? <><SectionTitle>Top Vertrieb</SectionTitle><Card style={{ padding: 0, overflow: "hidden" }}>{data.topSalesReps.length === 0 ? <Muted style={{ padding: 16 }}>Im gewählten Zeitraum liegen keine zugeordneten Bestellungen vor.</Muted> : data.topSalesReps.map((row: any, index: number) => <View key={row.userId} style={[styles.rankingRow, index > 0 && styles.activityBorder]}><Text style={styles.rank}>{index + 1}</Text><View style={{ flex: 1 }}><Text style={styles.activityTitle}>{row.name}</Text><Text style={styles.activityMeta}>{row.orders} Bestellungen · {row.activeCustomers} aktive Kunden</Text></View><View style={{ alignItems: "flex-end" }}><Text style={styles.activityTitle}>{euro(row.revenue)}</Text>{row.marginDataComplete ? <Text style={styles.activityMeta}>DB {euro(row.margin)}</Text> : <Text style={styles.activityMeta}>DB nicht vollständig</Text>}</View></View>)}</Card></> : null}
      {data.topCustomers?.length ? <><SectionTitle>Top Kunden</SectionTitle><Card style={{ padding: 0, overflow: "hidden" }}>{data.topCustomers.map((row: any, index: number) => <Pressable key={row.companyId} onPress={() => onCustomer(row.companyId)} style={[styles.rankingRow, index > 0 && styles.activityBorder]}><Text style={styles.rank}>{index + 1}</Text><View style={{ flex: 1 }}><Text style={styles.activityTitle}>{row.name}</Text><Text style={styles.activityMeta}>{row.orders} Bestellungen · Menge {num(row.quantity)}</Text></View><Text style={styles.activityTitle}>{euro(row.revenue)}</Text><ArrowRight size={16} color={colors.muted} /></Pressable>)}</Card></> : null}
      {data.managementAlerts?.length ? <><SectionTitle>Handlungsbedarf</SectionTitle><Card>{data.managementAlerts.slice(0, 10).map((alert: any) => <Pressable key={`${alert.type}-${alert.id}`} onPress={() => alert.companyId ? onCustomer(alert.companyId) : undefined} style={styles.systemRow}><Warning size={17} color={colors.warning} /><Text style={styles.systemText}>{alert.type === "invoice_overdue" ? `Rechnung ${alert.id} ist überfällig` : alert.title}</Text></Pressable>)}</Card></> : null}
    </>
  );
}

function CustomerDash({ data }: { data: any }) {
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <>
      <View style={styles.hero} testID="hero-customer">
        <View style={styles.heroIcon}>
          <Coffee size={24} color={colors.onBrand} weight="fill" />
        </View>
        <Text style={styles.heroLabel}>{data.companyName}</Text>
        <Text style={styles.heroValue}>{num(data.monthlyKg)} kg</Text>
        <Text style={styles.heroSub}>Ihr Monatsverbrauch</Text>
      </View>

      <View style={styles.kpiGrid}>
        <KPICard label="Mindestabnahme" value={`${num(data.minQtyMonth)} kg`} accent="primary" />
        <KPICard
          label="Offene Rechnungen"
          value={euro(data.openInvoices)}
          accent={data.openInvoices > 0 ? "warning" : "success"}
        />
      </View>

      {data.contract && (
        <>
          <SectionTitle style={{ marginTop: 8 }}>Ihr Vertrag</SectionTitle>
          <Card testID="customer-contract-card">
            <Text style={styles.cardTitle}>{data.contract.machine || "Kaffeeliefervertrag"}</Text>
            <InfoRow label="Preis" value={`${euro(data.contract.price)}/kg`} />
            <InfoRow label="Mindestabnahme" value={`${num(data.contract.minQtyMonth)} kg/Monat`} />
            <InfoRow label="Laufzeit" value={`${data.contract.termMonths} Monate`} />
            {data.contract.serviceRate ? (
              <InfoRow label="Service" value={`${euro(data.contract.serviceRate)}/Monat`} />
            ) : null}
          </Card>
        </>
      )}
    </>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  content: { padding: tokens.spacing.lg, paddingBottom: 36 },
  page: { gap: tokens.spacing.md },
  headerBtns: { flexDirection: "row", gap: 8 },
  hero: {
    backgroundColor: c.brand,
    borderRadius: tokens.radius.lg,
    padding: 22,
    gap: 2,
    borderWidth: 1,
    borderColor: c.border,
  },
  heroIcon: {
    width: 46,
    height: 46,
    borderRadius: 14,
    backgroundColor: c.inverseActive,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 12,
  },
  heroLabel: { fontSize: 14, color: c.inverseMuted, fontWeight: "600" },
  heroValue: { fontSize: 38, fontWeight: "800", color: c.onBrand, letterSpacing: -1, marginTop: 2 },
  heroSub: { fontSize: 13, color: c.inverseSubtle, marginTop: 6 },
  topGrid: { gap: 14 },
  topGridDesktop: { flexDirection: "row", alignItems: "stretch" },
  kpiArea: { flex: 1, gap: 12 },
  kpiGrid: { flexDirection: "row", gap: 12 },
  periodRow: { flexDirection: "row", gap: 8, justifyContent: "flex-end" },
  periodChip: { borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border },
  periodChipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  periodText: { color: c.onSurfaceSecondary, fontWeight: "700", fontSize: 13 },
  periodTextActive: { color: c.onBrandPrimary },
  rankingRow: { minHeight: 64, flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 10 },
  rank: { width: 24, color: c.brandPrimary, fontWeight: "900", fontSize: 16, textAlign: "center" },
  operationalGrid: { gap: 12 },
  operationalGridDesktop: { flexDirection: "row" },
  operationCard: { flex: 1 },
  operationRows: { gap: 2 },
  systemRow: { flexDirection: "row", alignItems: "center", gap: 9, paddingVertical: 3 },
  systemText: { color: c.onSurfaceSecondary, fontSize: 13.5, fontWeight: "600" },
  alert: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: c.surface,
    borderRadius: 18,
    padding: 16,
    borderWidth: 1,
  },
  alertIcon: { width: 36, height: 36, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  alertTitle: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  alertSub: { fontSize: 13, color: c.muted, marginTop: 2 },
  cardTitle: { fontSize: 16, fontWeight: "800", color: c.onSurface, marginBottom: 4 },
  followRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12 },
  followRowBorder: { borderTopWidth: 1, borderTopColor: c.divider },
  overdueDot: { width: 8, height: 8, borderRadius: 999 },
  followName: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  followSub: { fontSize: 13, color: c.muted, marginTop: 1 },
  activityRow: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 10 },
  activityBorder: { borderTopWidth: 1, borderTopColor: c.divider },
  activityIcon: { width: 34, height: 34, borderRadius: 9, backgroundColor: c.brandTertiary, alignItems: "center", justifyContent: "center" },
  activityTitle: { color: c.onSurface, fontSize: 14, fontWeight: "800" },
  activityMeta: { color: c.muted, fontSize: 12.5, marginTop: 2 },
}));
