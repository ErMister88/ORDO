import React from "react";
import { View, Text, Pressable } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { makeStyles, useTheme } from "@/src/theme";

export function ScreenHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
      <View style={{ flex: 1 }}>
        <Text style={styles.title}>{title}</Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      </View>
      {right}
    </View>
  );
}

export function HeaderButton({
  onPress,
  children,
  testID,
}: {
  onPress: () => void;
  children: React.ReactNode;
  testID?: string;
}) {
  const styles = useStyles();
  return (
    <Pressable onPress={onPress} style={styles.hbtn} testID={testID} hitSlop={8}>
      {children}
    </Pressable>
  );
}

const useStyles = makeStyles((c) => ({
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
  title: { fontSize: 28, fontWeight: "800", color: c.onSurface, letterSpacing: -0.6 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 3, fontWeight: "500" },
  hbtn: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: c.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
}));
