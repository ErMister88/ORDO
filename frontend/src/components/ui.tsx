import React from "react";
import { View, Text, Pressable, ActivityIndicator, TextInput } from "react-native";
import { makeStyles, useTheme } from "@/src/theme";

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------
export function Card({
  children,
  style,
  testID,
}: {
  children: React.ReactNode;
  style?: any;
  testID?: string;
}) {
  const styles = useStyles();
  return (
    <View style={[styles.card, style]} testID={testID}>
      {children}
    </View>
  );
}

// ---------------------------------------------------------------------------
// Section title
// ---------------------------------------------------------------------------
export function SectionTitle({ children, style }: { children: React.ReactNode; style?: any }) {
  const styles = useStyles();
  return <Text style={[styles.sectionTitle, style]}>{children}</Text>;
}

// ---------------------------------------------------------------------------
// KPI tile
// ---------------------------------------------------------------------------
export function KPICard({
  label,
  value,
  accent,
  testID,
}: {
  label: string;
  value: string;
  accent?: "primary" | "success" | "warning" | "error";
  testID?: string;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const accentColor =
    accent === "success"
      ? colors.success
      : accent === "warning"
      ? colors.warning
      : accent === "error"
      ? colors.error
      : colors.brandPrimary;
  return (
    <View style={styles.kpiCard} testID={testID}>
      <View style={[styles.kpiBar, { backgroundColor: accentColor }]} />
      <Text style={styles.kpiValue} numberOfLines={1} adjustsFontSizeToFit>
        {value}
      </Text>
      <Text style={styles.kpiLabel}>{label}</Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------
export function StatusBadge({ status, testID }: { status: string; testID?: string }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const map: Record<string, string> = {
    "Freigabe nötig": colors.warning,
    Freigegeben: colors.success,
    Versendet: colors.info,
    Angenommen: colors.success,
    Abgelehnt: colors.error,
    Entwurf: colors.muted,
    Neu: colors.info,
    Bestätigt: colors.info,
    Kommissioniert: colors.warning,
    Abgeschlossen: colors.success,
    Bezahlt: colors.success,
    Offen: colors.warning,
    "Überfällig": colors.error,
    Storniert: colors.error,
  };
  const dot = map[status] || colors.muted;
  return (
    <View style={styles.badge} testID={testID}>
      <View style={[styles.badgeDot, { backgroundColor: dot }]} />
      <Text style={[styles.badgeText, { color: dot }]}>{status}</Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------
export function Button({
  title,
  onPress,
  kind = "primary",
  loading,
  disabled,
  testID,
  style,
}: {
  title: string;
  onPress: () => void;
  kind?: "primary" | "secondary" | "danger" | "success";
  loading?: boolean;
  disabled?: boolean;
  testID?: string;
  style?: any;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const isDisabled = disabled || loading;
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={isDisabled}
      style={({ pressed }) => [
        styles.button,
        kind === "secondary" && styles.buttonSecondary,
        kind === "danger" && styles.buttonDanger,
        kind === "success" && styles.buttonSuccess,
        isDisabled && styles.buttonDisabled,
        pressed && !isDisabled && styles.buttonPressed,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={kind === "secondary" ? colors.brandPrimary : colors.onBrandPrimary} />
      ) : (
        <Text style={[styles.buttonText, kind === "secondary" && { color: colors.brandPrimary }]}>{title}</Text>
      )}
    </Pressable>
  );
}

// ---------------------------------------------------------------------------
// Input
// ---------------------------------------------------------------------------
export const Input = React.forwardRef<TextInput, any>(function Input({ style, ...props }, ref) {
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <TextInput
      ref={ref}
      placeholderTextColor={colors.muted}
      style={[styles.input, style]}
      {...props}
    />
  );
});

// ---------------------------------------------------------------------------
// Rows / labels
// ---------------------------------------------------------------------------
export function InfoRow({ label, value }: { label: string; value: string }) {
  const styles = useStyles();
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  );
}

export function Muted({ children, style, testID }: { children: React.ReactNode; style?: any; testID?: string }) {
  const styles = useStyles();
  return <Text testID={testID} style={[styles.muted, style]}>{children}</Text>;
}

export function EmptyState({ title, subtitle }: { title: string; subtitle?: string }) {
  const styles = useStyles();
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>{title}</Text>
      {subtitle ? <Text style={styles.emptySub}>{subtitle}</Text> : null}
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  card: {
    backgroundColor: c.surface,
    borderRadius: 22,
    padding: 18,
    borderWidth: 1,
    borderColor: c.border,
    gap: 10,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: "800",
    color: c.onSurface,
    letterSpacing: -0.3,
  },
  kpiCard: {
    flex: 1,
    backgroundColor: c.surface,
    borderRadius: 20,
    padding: 16,
    borderWidth: 1,
    borderColor: c.border,
    overflow: "hidden",
    minHeight: 108,
    justifyContent: "center",
  },
  kpiBar: {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: 5,
  },
  kpiValue: {
    fontSize: 29,
    fontWeight: "800",
    color: c.onSurface,
    letterSpacing: -0.6,
  },
  kpiLabel: {
    fontSize: 12.5,
    color: c.muted,
    marginTop: 6,
    fontWeight: "600",
  },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    backgroundColor: c.surfaceTertiary,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
    gap: 6,
  },
  badgeDot: { width: 7, height: 7, borderRadius: 999 },
  badgeText: { fontSize: 12, fontWeight: "700" },
  button: {
    backgroundColor: c.brandPrimary,
    paddingVertical: 16,
    paddingHorizontal: 18,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 52,
  },
  buttonSecondary: {
    backgroundColor: c.surfaceTertiary,
    borderWidth: 1,
    borderColor: c.border,
  },
  buttonDanger: { backgroundColor: c.error },
  buttonSuccess: { backgroundColor: c.success },
  buttonDisabled: { opacity: 0.5 },
  buttonPressed: { opacity: 0.85 },
  buttonText: { color: c.onBrandPrimary, fontWeight: "700", fontSize: 15 },
  input: {
    backgroundColor: c.surfaceTertiary,
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingVertical: 15,
    color: c.onSurface,
    fontSize: 16,
    borderWidth: 1,
    borderColor: "transparent",
  },
  infoRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 6,
  },
  infoLabel: { fontSize: 14, color: c.muted, fontWeight: "500" },
  infoValue: { fontSize: 14, color: c.onSurface, fontWeight: "700" },
  muted: { fontSize: 14, color: c.muted, lineHeight: 20 },
  empty: { alignItems: "center", justifyContent: "center", paddingVertical: 48, gap: 6 },
  emptyTitle: { fontSize: 16, fontWeight: "700", color: c.onSurface },
  emptySub: { fontSize: 14, color: c.muted, textAlign: "center" },
}));
