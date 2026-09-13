import { Platform } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Notifications from "expo-notifications";
import { API_BASE } from "@/src/api/client";

const DEVICE_ID_KEY = "push_device_id";

async function deviceId(): Promise<string> {
  let id = await AsyncStorage.getItem(DEVICE_ID_KEY);
  if (!id) {
    id = `dev-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    await AsyncStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
}

/** Request permission, get native token, register with backend. Non-blocking. */
export async function registerForPush(): Promise<void> {
  if (Platform.OS === "web") return;
  try {
    const { status } = await Notifications.requestPermissionsAsync();
    if (status !== "granted") return;
    const tokenResp = await Notifications.getDevicePushTokenAsync();
    const user_id = await deviceId();
    await fetch(`${API_BASE}/api/register-push`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id, platform: Platform.OS, device_token: tokenResp.data }),
    });
  } catch {
    /* push registration is best-effort */
  }
}
