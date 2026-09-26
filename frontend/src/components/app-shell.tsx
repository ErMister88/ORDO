import { useEffect, useState, type ReactNode } from "react";
import { ActivityIndicator, Pressable, ScrollView, View, useWindowDimensions } from "react-native";
import { usePathname, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import {
  ChartBar, Coffee, FileText, Gauge, Package, Receipt, ShoppingBag,
  Storefront, Tag, Users, UsersThree, ClockCounterClockwise, SignOut, Plus, Calculator, CheckSquare, Bell,
} from "phosphor-react-native";

import { apiGet } from "@/src/api/client";
import { useAuth } from "@/src/auth/auth";
import { canAccessRoute, deniedRouteTarget, isPublicRoute } from "@/src/auth/route-access";
import { makeStyles, tokens, useTheme } from "@/src/theme";
import { languageLabels, LocalizedText as Text, useI18n, type SupportedLanguage } from "@/src/i18n";

type NavItem = { label: string; path: string; icon: typeof Gauge; match?: string[] };
type NavGroup = { label: string; items: NavItem[] };

function groups(role?: string): NavGroup[] {
  const staff = role === "admin" || role === "sales";
  if (staff) {
    return [
      { label: "Hauptbereich", items: [
        { label: "Dashboard", path: "/(tabs)", icon: Gauge, match: ["/(tabs)", "/(tabs)/index"] },
        { label: "Kunden", path: "/(tabs)/kunden", icon: Users, match: ["/(tabs)/kunden", "/kunde"] },
        { label: "Produktkatalog", path: "/katalog", icon: Package },
        ...(role === "admin" ? [{ label: "Vertrieb", path: "/(tabs)/auswertungen", icon: ChartBar }] : []),
        { label: "Bestellungen", path: "/(tabs)/bestellungen", icon: ShoppingBag },
        { label: "Angebote", path: "/(tabs)/angebote", icon: Tag },
        { label: "Rechnungen & Verträge", path: "/(tabs)/mehr", icon: FileText },
        { label: role === "admin" ? "Maschinen & Anfragen" : "Maschinen", path: role === "admin" ? "/maschinen-admin" : "/maschinen", icon: Coffee },
      ] },
      { label: "CRM", items: [
        { label: "Aktivitäten & Aufgaben", path: "/(tabs)/kunden", icon: CheckSquare },
      ] },
      ...(role === "admin" ? [
        { label: "Management", items: [
          { label: "Auswertungen", path: "/(tabs)/auswertungen", icon: ChartBar },
          { label: "Kalkulation & Produkte", path: "/produkte", icon: Calculator },
          { label: "Shop-Verwaltung", path: "/shop-admin", icon: ShoppingBag },
        ] },
        { label: "System", items: [
          { label: "Benutzer", path: "/benutzer", icon: UsersThree },
          { label: "Abos", path: "/abos", icon: Receipt },
          { label: "Audit-Log", path: "/audit", icon: ClockCounterClockwise },
          { label: "Einstellungen", path: "/einstellungen", icon: Storefront },
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

export function AppShell({ children }: { children: ReactNode }) {
  const styles = useStyles();
  const { width } = useWindowDimensions();
  const path = usePathname();
  const router = useRouter();
  const { user, loading } = useAuth();
  const desktop = width >= tokens.layout.desktop;
  const publicRoute = isPublicRoute(path);
  const allowed = canAccessRoute(path, user?.role ?? null);
  const mustChangePassword = Boolean(user?.must_change_password && !path.startsWith("/passwort-aendern"));

  useEffect(() => {
    if (loading || publicRoute) return;
    if (mustChangePassword) router.replace("/passwort-aendern?forced=1");
    else if (!allowed) router.replace(deniedRouteTarget(user?.role ?? null));
  }, [allowed, loading, mustChangePassword, publicRoute, router, user?.role]);

  if (!publicRoute && (loading || mustChangePassword || !allowed)) {
    return (
      <View style={styles.guardLoading} testID="route-guard-loading">
        <ActivityIndicator size="large" />
      </View>
    );
  }
  if (!desktop || !user || publicRoute) {
    return <View style={{ flex: 1 }}><View style={{ flex: 1 }}>{children}</View>{user && !publicRoute ? <NotificationBell /> : null}<LanguageSwitcher /></View>;
  }
  return <View style={{ flex: 1, flexDirection: "row" }}><DesktopSidebar /><View style={{ flex: 1 }}>{children}</View><NotificationBell /><LanguageSwitcher /></View>;
}

function NotificationBell() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const notifications = useQuery<Array<{ id: string; readAt?: string | null }>>({
    queryKey: ["notifications"], queryFn: () => apiGet("/notifications"), refetchInterval: 60000,
  });
  const unread = (notifications.data ?? []).filter((row) => !row.readAt).length;
  return <Pressable accessibilityLabel="Benachrichtigungen" testID="notification-bell" onPress={() => router.push("/benachrichtigungen")} style={styles.notificationBell}>
    <Bell size={19} color={colors.onSurface} weight={unread ? "fill" : "regular"} />
    {unread ? <View style={styles.notificationBadge}><Text style={styles.notificationBadgeText}>{Math.min(unread, 99)}</Text></View> : null}
  </Pressable>;
}

function LanguageSwitcher() {
  const styles = useStyles();
  const { language, setLanguage, t } = useI18n();
  return (
    <View style={styles.languageSwitcher} testID="language-switcher" accessibilityLabel={t("Sprache")}>
      {(Object.keys(languageLabels) as SupportedLanguage[]).map((value) => (
        <Pressable
          key={value}
          accessibilityRole="button"
          accessibilityState={{ selected: language === value }}
          onPress={() => { void setLanguage(value); }}
          style={[styles.languageButton, language === value && styles.languageButtonActive]}
        >
          <Text style={[styles.languageText, language === value && styles.languageTextActive]}>{languageLabels[value]}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function DesktopSidebar() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user, signOut } = useAuth();
  const [quickOpen, setQuickOpen] = useState(false);
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
      <Pressable testID="global-new" onPress={() => setQuickOpen((value) => !value)} style={styles.quickButton}>
        <Plus size={18} color={colors.onBrandPrimary} weight="bold" /><Text style={styles.quickButtonText}>Neu</Text>
      </Pressable>
      {quickOpen ? <View style={styles.quickMenu}>
        {[
          ["Neuer Kunde", "/(tabs)/kunden?new=1"],
          ["Neue Bestellung", "/(tabs)/bestellungen"],
          ["Neues Angebot", "/(tabs)/angebote"],
          ["Neue Rechnung", "/(tabs)/bestellungen?createInvoice=1"],
          ["Neue Aufgabe", "/(tabs)/kunden"],
        ].map(([label, target]) => <Pressable key={label} onPress={() => { setQuickOpen(false); router.push(target as any); }} style={styles.quickItem}><Text style={styles.quickItemText}>{label}</Text></Pressable>)}
      </View> : null}
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
  guardLoading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: c.surfaceSecondary },
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
  quickButton: { marginHorizontal: 6, marginBottom: 16, minHeight: 42, borderRadius: 10, backgroundColor: c.brandPrimary, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7 },
  quickButtonText: { color: c.onBrandPrimary, fontWeight: "900" },
  quickMenu: { marginHorizontal: 6, marginTop: -10, marginBottom: 14, borderRadius: 10, padding: 6, backgroundColor: c.inverseActive },
  quickItem: { minHeight: 34, justifyContent: "center", paddingHorizontal: 10 },
  quickItemText: { color: c.onSurfaceInverse, fontWeight: "700", fontSize: 12.5 },
  profileName: { color: c.onSurfaceInverse, fontSize: 12.5, fontWeight: "700" },
  profileRole: { color: c.inverseSubtle, fontSize: 11, marginTop: 1 },
  languageSwitcher: {
    position: "absolute", top: 10, right: 12, zIndex: 100,
    flexDirection: "row", gap: 2, padding: 3, borderRadius: 10,
    backgroundColor: c.surface, borderWidth: 1, borderColor: c.border,
    ...tokens.shadow,
  },
  notificationBell: {
    position: "absolute", top: 10, right: 126, zIndex: 100,
    width: 36, height: 36, borderRadius: 10, backgroundColor: c.surface,
    borderWidth: 1, borderColor: c.border, alignItems: "center", justifyContent: "center",
    ...tokens.shadow,
  },
  notificationBadge: { position: "absolute", top: -5, right: -5, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4, backgroundColor: c.error, alignItems: "center", justifyContent: "center" },
  notificationBadgeText: { color: c.onError, fontSize: 10, fontWeight: "900" },
  languageButton: { minWidth: 30, height: 28, borderRadius: 7, alignItems: "center", justifyContent: "center" },
  languageButtonActive: { backgroundColor: c.brandPrimary },
  languageText: { fontSize: 11, fontWeight: "800", color: c.muted },
  languageTextActive: { color: c.onBrandPrimary },
}));
