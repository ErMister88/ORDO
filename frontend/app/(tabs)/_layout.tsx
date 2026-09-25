import { Tabs } from "expo-router";
import { Platform, useWindowDimensions } from "react-native";
import {
  House,
  Users,
  Tag,
  ChartBar,
  ShoppingCart,
  DotsThreeCircle,
} from "phosphor-react-native";

import { tokens, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { useI18n } from "@/src/i18n";

export default function TabsLayout() {
  const { colors } = useTheme();
  const { t } = useI18n();
  const { user } = useAuth();
  const role = user?.role ?? "customer";
  const isStaff = role === "admin" || role === "sales";
  const { width } = useWindowDimensions();
  const desktop = width >= tokens.layout.desktop;

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brandPrimary,
        tabBarInactiveTintColor: colors.muted,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          ...(desktop ? { display: "none" } : Platform.OS === "web" ? { height: 64 } : {}),
        },
        tabBarItemStyle: { alignSelf: "center" },
        tabBarLabelStyle: { fontSize: 11, fontWeight: "700" },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: t("Übersicht"),
          tabBarIcon: ({ color, size }) => <House size={size} color={String(color)} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="kunden"
        options={{
          title: t("Kunden"),
          href: isStaff ? "/(tabs)/kunden" : null,
          tabBarIcon: ({ color, size }) => <Users size={size} color={String(color)} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="angebote"
        options={{
          title: t("Angebote"),
          tabBarIcon: ({ color, size }) => <Tag size={size} color={String(color)} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="auswertungen"
        options={{
          title: t("Auswertungen"),
          href: isStaff ? "/(tabs)/auswertungen" : null,
          tabBarIcon: ({ color, size }) => <ChartBar size={size} color={String(color)} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="bestellungen"
        options={{
          title: t("Bestellungen"),
          href: !isStaff ? "/(tabs)/bestellungen" : null,
          tabBarIcon: ({ color, size }) => <ShoppingCart size={size} color={String(color)} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="mehr"
        options={{
          title: t("Mehr"),
          href: "/(tabs)/mehr",
          tabBarIcon: ({ color, size }) => <DotsThreeCircle size={size} color={String(color)} weight="fill" />,
        }}
      />
    </Tabs>
  );
}
