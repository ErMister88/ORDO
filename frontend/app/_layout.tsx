import { QueryClientProvider } from "@tanstack/react-query";
import { Stack } from "expo-router";
import { LogBox } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";

import { ErrorBoundary } from "@/src/components/error-boundary";
import { queryClient } from "@/src/query-client";
import { AuthProvider } from "@/src/auth/auth";
import { CartProvider } from "@/src/shop/cart";

LogBox.ignoreAllLogs(true);

export default function RootLayout() {
  return (
    <ErrorBoundary>
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <QueryClientProvider client={queryClient}>
            <AuthProvider>
              <CartProvider>
                <StatusBar style="dark" />
                <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: "#F4F7FB" } }}>
                  <Stack.Screen name="index" />
                  <Stack.Screen name="login" />
                  <Stack.Screen name="(tabs)" />
                  <Stack.Screen name="kunde/[id]" options={{ presentation: "card" }} />
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
                </Stack>
              </CartProvider>
            </AuthProvider>
          </QueryClientProvider>
        </SafeAreaProvider>
      </GestureHandlerRootView>
    </ErrorBoundary>
  );
}
