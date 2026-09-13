import { Tabs } from "expo-router";
import { Platform } from "react-native";
import {
  House,
  Users,
  Tag,
  ChartBar,
  ShoppingCart,
  DotsThreeCircle,
} from "phosphor-react-native";

import { useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";

export default function TabsLayout() {
  const { colors } = useTheme();
  const { user } = useAuth();
  const role = user?.role ?? "customer";
  const isStaff = role === "admin" || role === "sales";

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brandPrimary,
        tabBarInactiveTintColor: colors.muted,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          ...(Platform.OS === "web" ? { height: 64 } : {}),
        },
        tabBarItemStyle: { alignSelf: "center" },
        tabBarLabelStyle: { fontSize: 11, fontWeight: "700" },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Übersicht",
          tabBarIcon: ({ color, size }) => <House size={size} color={color} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="kunden"
        options={{
          title: "Kunden",
          href: isStaff ? "/(tabs)/kunden" : null,
          tabBarIcon: ({ color, size }) => <Users size={size} color={color} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="angebote"
        options={{
          title: "Angebote",
          tabBarIcon: ({ color, size }) => <Tag size={size} color={color} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="auswertungen"
        options={{
          title: "Auswertungen",
          href: isStaff ? "/(tabs)/auswertungen" : null,
          tabBarIcon: ({ color, size }) => <ChartBar size={size} color={color} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="bestellungen"
        options={{
          title: "Bestellungen",
          href: !isStaff ? "/(tabs)/bestellungen" : null,
          tabBarIcon: ({ color, size }) => <ShoppingCart size={size} color={color} weight="fill" />,
        }}
      />
      <Tabs.Screen
        name="mehr"
        options={{
          title: "Mehr",
          href: !isStaff ? "/(tabs)/mehr" : null,
          tabBarIcon: ({ color, size }) => <DotsThreeCircle size={size} color={color} weight="fill" />,
        }}
      />
    </Tabs>
  );
}
