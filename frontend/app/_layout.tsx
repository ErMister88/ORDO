import { useEffect } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { LogBox, Platform } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import * as Notifications from "expo-notifications";
import * as Linking from "expo-linking";

import { ErrorBoundary } from "@/src/components/error-boundary";
import { queryClient } from "@/src/query-client";
import { AuthProvider } from "@/src/auth/auth";
import { CartProvider } from "@/src/shop/cart";
import { AppShell } from "@/src/components/app-shell";
import { useTheme } from "@/src/theme";
import { registerForPush } from "@/src/push";
import { I18nProvider } from "@/src/i18n";

LogBox.ignoreAllLogs(true);

// Foreground display behaviour — module scope, before any component
if (Platform.OS !== "web") {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowAlert: true,
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

// Android channel — module scope
if (Platform.OS === "android") {
  Notifications.setNotificationChannelAsync("default", {
    name: "Default",
    importance: Notifications.AndroidImportance.MAX,
    sound: "default",
  });
}

export default function RootLayout() {
  const router = useRouter();
  const { colors } = useTheme();

  useEffect(() => {
    if (Platform.OS === "web") return;

    registerForPush();

    const openFromData = (data: any) => {
      const url = data?.deeplink || data?.action_url;
      if (!url) return;
      url.startsWith("http") ? Linking.openURL(url) : router.push(url);
    };

    const tapSub = Notifications.addNotificationResponseReceivedListener((response) => {
      openFromData(response.notification.request.content.data || {});
    });

    Notifications.getLastNotificationResponseAsync().then((response) => {
      if (response) openFromData(response.notification.request.content.data || {});
    });

    return () => {
      tapSub.remove();
    };
  }, []);

  return (
    <ErrorBoundary>
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <I18nProvider>
            <QueryClientProvider client={queryClient}>
              <AuthProvider>
                <CartProvider>
                  <StatusBar style="dark" />
                  <AppShell><Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.surfaceSecondary } }}>
                  <Stack.Screen name="index" />
                  <Stack.Screen name="login" />
                  <Stack.Screen name="(tabs)" />
                  <Stack.Screen name="kunde/[id]" options={{ presentation: "card" }} />
                  <Stack.Screen name="katalog" options={{ presentation: "card" }} />
                  <Stack.Screen name="vertrieb/[id]" options={{ presentation: "card" }} />
                  <Stack.Screen name="bestellung/[id]" options={{ presentation: "card" }} />
                  <Stack.Screen name="produkte" options={{ presentation: "card" }} />
                  <Stack.Screen name="benutzer" options={{ presentation: "card" }} />
                  <Stack.Screen name="abos" options={{ presentation: "card" }} />
                  <Stack.Screen name="audit" options={{ presentation: "card" }} />
                  <Stack.Screen name="shop-admin" options={{ presentation: "card" }} />
                  <Stack.Screen name="shop/index" options={{ presentation: "card" }} />
                  <Stack.Screen name="shop/warenkorb" options={{ presentation: "card" }} />
                  <Stack.Screen name="shop/konto" options={{ presentation: "card" }} />
                  <Stack.Screen name="passwort-vergessen" options={{ presentation: "card" }} />
                  <Stack.Screen name="passwort-aendern" options={{ presentation: "card" }} />
                  <Stack.Screen name="legal/impressum" options={{ presentation: "card" }} />
                  <Stack.Screen name="legal/datenschutz" options={{ presentation: "card" }} />
                  <Stack.Screen name="legal/agb" options={{ presentation: "card" }} />
                  <Stack.Screen name="legal/widerruf" options={{ presentation: "card" }} />
                  <Stack.Screen name="maschinen" options={{ presentation: "card" }} />
                  <Stack.Screen name="maschinen-admin" options={{ presentation: "card" }} />
                  <Stack.Screen name="einstellungen" options={{ presentation: "card" }} />
                  <Stack.Screen name="systembetrieb" options={{ presentation: "card" }} />
                  <Stack.Screen name="zahlung" options={{ presentation: "card" }} />
                  </Stack></AppShell>
                </CartProvider>
              </AuthProvider>
            </QueryClientProvider>
          </I18nProvider>
        </SafeAreaProvider>
      </GestureHandlerRootView>
    </ErrorBoundary>
  );
}
