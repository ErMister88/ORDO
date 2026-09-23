import type { ReactNode } from "react";
import { Pressable, ScrollView, Text, View, useWindowDimensions } from "react-native";
import { usePathname, useRouter } from "expo-router";
import {
  ChartBar, Coffee, FileText, Gauge, Package, Receipt, ShoppingBag,
  Storefront, Tag, Users, UsersThree, ClockCounterClockwise, SignOut,
} from "phosphor-react-native";

import { useAuth } from "@/src/auth/auth";
import { makeStyles, tokens, useTheme } from "@/src/theme";

type NavItem = { label: string; path: string; icon: typeof Gauge; match?: string[] };
type NavGroup = { label: string; items: NavItem[] };

function groups(role?: string): NavGroup[] {
  const staff = role === "admin" || role === "sales";
  if (staff) {
    return [
      { label: "Arbeitsbereich", items: [
        { label: "Dashboard", path: "/(tabs)", icon: Gauge, match: ["/(tabs)", "/(tabs)/index"] },
        { label: "Auswertungen", path: "/(tabs)/auswertungen", icon: ChartBar },
      ] },
      { label: "Vertrieb", items: [
        { label: "Kunden", path: "/(tabs)/kunden", icon: Users, match: ["/(tabs)/kunden", "/kunde"] },
        { label: "Angebote", path: "/(tabs)/angebote", icon: Tag },
      ] },
      { label: "Operations", items: [
        ...(role === "admin" ? [{ label: "Produkte & Preise", path: "/produkte", icon: Package }] : []),
        { label: "Verträge & Finanzen", path: "/(tabs)/mehr", icon: FileText },
        { label: role === "admin" ? "Maschinen & Anfragen" : "Maschinen", path: role === "admin" ? "/maschinen-admin" : "/maschinen", icon: Coffee },
      ] },
      ...(role === "admin" ? [
        { label: "Commerce", items: [
          { label: "Shop-Verwaltung", path: "/shop-admin", icon: ShoppingBag },
          { label: "B2C-Shop öffnen", path: "/shop", icon: Storefront },
        ] },
        { label: "Verwaltung", items: [
          { label: "Benutzer", path: "/benutzer", icon: UsersThree },
          { label: "Abos", path: "/abos", icon: Receipt },
          { label: "Audit-Log", path: "/audit", icon: ClockCounterClockwise },
        ] },
      ] : []),
    ];
  }
  return [
    { label: "Kundenportal", items: [
      { label: "Übersicht", path: "/(tabs)", icon: Gauge, match: ["/(tabs)", "/(tabs)/index"] },
      { label: "Bestellungen", path: "/(tabs)/bestellungen", icon: ShoppingBag },
      { label: "Angebote", path: "/(tabs)/angebote", icon: Tag },
      { label: "Verträge & Rechnungen", path: "/(tabs)/mehr", icon: FileText },
      { label: "Maschinen", path: "/maschinen", icon: Coffee },
      { label: "B2C-Shop", path: "/shop", icon: Storefront },
    ] },
  ];
}

const publicPath = (path: string) => path === "/" || path.startsWith("/login") || path === "/shop" || path.startsWith("/shop/") || path.startsWith("/legal") || path.startsWith("/passwort-vergessen");

export function AppShell({ children }: { children: ReactNode }) {
  const { width } = useWindowDimensions();
  const path = usePathname();
  const { user } = useAuth();
  const desktop = width >= tokens.layout.desktop;
  if (!desktop || !user || publicPath(path)) return <>{children}</>;
  return <View style={{ flex: 1, flexDirection: "row" }}><DesktopSidebar />{children}</View>;
}

function DesktopSidebar() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user, signOut } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const active = (item: NavItem) => (item.match ?? [item.path]).some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
  const logout = async () => { await signOut(); router.replace("/login"); };
  return (
    <View style={styles.sidebar} testID="desktop-sidebar">
      <View style={styles.brandBlock}>
        <View style={styles.mark}><Text style={styles.markText}>O</Text></View>
        <View><Text style={styles.brand}>ORDO</Text><Text style={styles.tenant}>S&S coffee and more</Text></View>
      </View>
      <ScrollView style={styles.nav} contentContainerStyle={styles.navContent} showsVerticalScrollIndicator={false}>
        {groups(user?.role).map((group) => (
          <View key={group.label} style={styles.group}>
            <Text style={styles.groupLabel}>{group.label}</Text>
            {group.items.map((item) => {
              const Icon = item.icon; const selected = active(item);
              return (
                <Pressable key={item.path + item.label} onPress={() => router.push(item.path as any)} style={[styles.item, selected && styles.itemActive]}>
                  <Icon size={18} color={selected ? colors.onSurfaceInverse : colors.inverseMuted} weight={selected ? "fill" : "regular"} />
                  <Text style={[styles.itemText, selected && styles.itemTextActive]}>{item.label}</Text>
                </Pressable>
              );
            })}
          </View>
        ))}
      </ScrollView>
      <View style={styles.profile}>
        <View style={styles.avatar}><Text style={styles.avatarText}>{user?.name?.charAt(0)?.toUpperCase()}</Text></View>
        <View style={{ flex: 1 }}><Text style={styles.profileName} numberOfLines={1}>{user?.name}</Text><Text style={styles.profileRole}>{user?.role === "admin" ? "Administrator" : user?.role === "sales" ? "Vertrieb" : "Kunde"}</Text></View>
        <Pressable onPress={logout} hitSlop={8}><SignOut size={18} color={colors.inverseMuted} /></Pressable>
      </View>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  sidebar: { width: tokens.layout.sidebar, backgroundColor: c.surfaceInverse, paddingHorizontal: 16, paddingVertical: 20, borderRightWidth: 1, borderRightColor: c.inverseBorder },
  brandBlock: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 8, paddingBottom: 24 },
  mark: { width: 38, height: 38, borderRadius: 10, backgroundColor: c.brandPrimary, alignItems: "center", justifyContent: "center" },
  markText: { color: c.onBrandPrimary, fontWeight: "900", fontSize: 20 },
  brand: { color: c.onSurfaceInverse, fontSize: 19, fontWeight: "900", letterSpacing: 1.2 },
  tenant: { color: c.inverseSubtle, fontSize: 11, marginTop: 1 },
  nav: { flex: 1 },
  navContent: { gap: 20, paddingBottom: 16 },
  group: { gap: 3 },
  groupLabel: { color: c.inverseLabel, fontSize: 10, fontWeight: "800", textTransform: "uppercase", letterSpacing: 1.1, paddingHorizontal: 10, marginBottom: 5 },
  item: { minHeight: 40, borderRadius: 8, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", gap: 10 },
  itemActive: { backgroundColor: c.inverseActive },
  itemText: { color: c.inverseMuted, fontSize: 13.5, fontWeight: "600" },
  itemTextActive: { color: c.onSurfaceInverse, fontWeight: "700" },
  profile: { flexDirection: "row", alignItems: "center", gap: 10, borderTopWidth: 1, borderTopColor: c.inverseBorder, paddingTop: 16 },
  avatar: { width: 34, height: 34, borderRadius: 9, backgroundColor: c.inverseActive, alignItems: "center", justifyContent: "center" },
  avatarText: { color: c.onSurfaceInverse, fontWeight: "800" },
  profileName: { color: c.onSurfaceInverse, fontSize: 12.5, fontWeight: "700" },
  profileRole: { color: c.inverseSubtle, fontSize: 11, marginTop: 1 },
}));
