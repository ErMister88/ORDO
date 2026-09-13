import { useEffect, useMemo, useState } from "react";
import { View, Text, ScrollView, Pressable, useWindowDimensions } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { BarChart, LineChart } from "react-native-gifted-charts";
import { TrendUp } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet } from "@/src/api/client";
import { euro, num } from "@/src/lib/format";
import { ScreenHeader } from "@/src/components/screen-header";
import { Card, SectionTitle, Muted } from "@/src/components/ui";

const TIMEFRAMES = [
  { key: 3, label: "3M" },
  { key: 6, label: "6M" },
  { key: 12, label: "12M" },
];
const METRICS = [
  { key: "revenue", label: "Umsatz" },
  { key: "margin", label: "Marge" },
  { key: "kg", label: "Menge" },
];

export default function Auswertungen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { width } = useWindowDimensions();
  const [months, setMonths] = useState(6);
  const [metric, setMetric] = useState<"revenue" | "margin" | "kg">("revenue");

  // Deckungsbeitrag / Marge is internal — sales never sees it
  const metrics = isAdmin ? METRICS : METRICS.filter((m) => m.key !== "margin");
  useEffect(() => {
    if (!isAdmin && metric === "margin") setMetric("revenue");
  }, [isAdmin, metric]);

  const { data, isLoading } = useQuery({
    queryKey: ["analytics", months],
    queryFn: () => apiGet(`/analytics?months=${months}`),
  });

  const series = data?.series ?? [];
  const chartWidth = width - 40 - 32; // screen padding + card padding

  const lineData = useMemo(
    () =>
      series.map((s: any) => ({
        value: s[metric],
        label: s.label,
        dataPointText: "",
      })),
    [series, metric]
  );

  const barData = useMemo(
    () =>
      series.map((s: any) => ({
        value: s.kg,
        label: s.label,
        frontColor: colors.brandSecondary,
      })),
    [series, colors]
  );

  const maxVal = Math.max(1, ...series.map((s: any) => s[metric]));
  const fmt = (v: number) => (metric === "kg" ? `${num(v)}` : euro(v));

  return (
    <View style={styles.root}>
      <ScreenHeader title="Auswertungen" subtitle={isAdmin ? "Umsatz, Marge & Menge" : "Umsatz & Menge"} />
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {/* Timeframe */}
        <View style={styles.segment}>
          {TIMEFRAMES.map((t) => {
            const active = months === t.key;
            return (
              <Pressable
                key={t.key}
                testID={`timeframe-${t.key}`}
                style={[styles.segItem, active && styles.segItemActive]}
                onPress={() => setMonths(t.key)}
              >
                <Text style={[styles.segText, active && styles.segTextActive]}>{t.label}</Text>
              </Pressable>
            );
          })}
        </View>

        {/* Summary */}
        <View style={styles.summaryRow}>
          <View style={[styles.summaryCard, { backgroundColor: colors.brand }]}>
            <Text style={styles.summaryLabel}>Umsatz gesamt</Text>
            <Text style={styles.summaryValue}>{euro(data?.totalRevenue ?? 0)}</Text>
            {isAdmin && data?.showMargin ? (
              <View style={styles.summaryPill}>
                <TrendUp size={13} color={colors.onSuccess} weight="bold" />
                <Text style={styles.summaryPillText}>Marge {num(data?.marginPct ?? 0, 1)}%</Text>
              </View>
            ) : null}
          </View>
        </View>

        {/* Metric toggle */}
        <View style={styles.metricRow}>
          {metrics.map((m) => {
            const active = metric === m.key;
            return (
              <Pressable
                key={m.key}
                testID={`metric-${m.key}`}
                style={[styles.metricChip, active && styles.metricChipActive]}
                onPress={() => setMetric(m.key as any)}
              >
                <Text style={[styles.metricText, active && styles.metricTextActive]}>{m.label}</Text>
              </Pressable>
            );
          })}
        </View>

        <SectionTitle>{METRICS.find((m) => m.key === metric)?.label} pro Monat</SectionTitle>
        <Card testID="line-chart-card" style={{ paddingVertical: 20 }}>
          {isLoading || series.length === 0 ? (
            <Muted>Nicht genug Daten</Muted>
          ) : (
            <LineChart
              data={lineData}
              width={chartWidth}
              height={180}
              maxValue={maxVal * 1.2}
              noOfSections={4}
              yAxisThickness={0}
              xAxisThickness={0}
              hideRules={false}
              rulesColor={colors.divider}
              color={colors.brandSecondary}
              thickness={3}
              startFillColor={colors.brandSecondary}
              endFillColor={colors.surface}
              startOpacity={0.25}
              endOpacity={0.02}
              areaChart
              curved
              dataPointsColor={colors.brandPrimary}
              dataPointsRadius={4}
              xAxisLabelTextStyle={{ color: colors.muted, fontSize: 11 }}
              yAxisTextStyle={{ color: colors.muted, fontSize: 10 }}
              formatYLabel={(v: string) => (metric === "kg" ? v : `${Math.round(Number(v))}`)}
              adjustToWidth
            />
          )}
        </Card>

        <SectionTitle>Menge (kg) pro Monat</SectionTitle>
        <Card testID="bar-chart-card" style={{ paddingVertical: 20 }}>
          {isLoading || series.length === 0 ? (
            <Muted>Nicht genug Daten</Muted>
          ) : (
            <BarChart
              data={barData}
              width={chartWidth}
              height={160}
              barWidth={Math.max(14, chartWidth / (series.length * 2.2))}
              spacing={Math.max(10, chartWidth / (series.length * 3))}
              barBorderRadius={6}
              yAxisThickness={0}
              xAxisThickness={0}
              rulesColor={colors.divider}
              xAxisLabelTextStyle={{ color: colors.muted, fontSize: 11 }}
              yAxisTextStyle={{ color: colors.muted, fontSize: 10 }}
              noOfSections={4}
              adjustToWidth
            />
          )}
        </Card>

        <SectionTitle>Monatswerte</SectionTitle>
        <Card style={{ padding: 6 }}>
          {series.map((s: any, i: number) => (
            <View key={s.label + i} style={[styles.tableRow, i > 0 && styles.tableRowBorder]}>
              <Text style={styles.tableMonth}>{s.label}</Text>
              <Text style={styles.tableVal}>{fmt(s[metric])}</Text>
            </View>
          ))}
        </Card>
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  segment: { flexDirection: "row", backgroundColor: c.surfaceTertiary, borderRadius: 12, padding: 4, gap: 4 },
  segItem: { flex: 1, paddingVertical: 10, borderRadius: 9, alignItems: "center" },
  segItemActive: { backgroundColor: c.surface, elevation: 1 },
  segText: { fontSize: 14, fontWeight: "700", color: c.muted },
  segTextActive: { color: c.onSurface },
  summaryRow: { flexDirection: "row", gap: 12 },
  summaryCard: { flex: 1, borderRadius: 20, padding: 18, gap: 4 },
  summaryLabel: { fontSize: 14, color: "rgba(255,255,255,0.75)", fontWeight: "600" },
  summaryValue: { fontSize: 30, fontWeight: "800", color: c.onBrand, letterSpacing: -0.8 },
  summaryPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    alignSelf: "flex-start",
    backgroundColor: c.success,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginTop: 6,
  },
  summaryPillText: { color: c.onSuccess, fontSize: 12, fontWeight: "700" },
  metricRow: { flexDirection: "row", gap: 8 },
  metricChip: {
    flex: 1,
    height: 38,
    borderRadius: 999,
    backgroundColor: c.surfaceTertiary,
    borderWidth: 1,
    borderColor: c.border,
    alignItems: "center",
    justifyContent: "center",
  },
  metricChipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  metricText: { fontSize: 13, fontWeight: "700", color: c.onSurfaceTertiary },
  metricTextActive: { color: c.onBrandPrimary },
  tableRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: 12 },
  tableRowBorder: { borderTopWidth: 1, borderTopColor: c.divider },
  tableMonth: { fontSize: 14, fontWeight: "600", color: c.onSurfaceSecondary },
  tableVal: { fontSize: 14, fontWeight: "800", color: c.onSurface },
}));
