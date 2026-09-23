// Design tokens for this app. Light theme only.Always modify the colors and theme to Dark, Light or Dark and Light according to the design guidelines.
//
// The keys match the "color" block of /app/design_guidelines.json. Fill the
// values from that file (or from the user's brand colors). Keep every key; do
// not add a second theme or colors file; do not write color literals in
// components.
//
// How the names work: a plain key is a background, and its `on` partner is the
// text or icon color that sits on top of it. Always use them as a pair.
//   <View style={{ backgroundColor: colors.brandPrimary }}>
//     <Text style={{ color: colors.onBrandPrimary }}>Continue</Text>
//   </View>
//
// Styling a screen or component: build the sheet with makeStyles so colors
// and layout live together and follow the active scheme:
//   const useStyles = makeStyles((colors) => ({
//     card: { backgroundColor: colors.surfaceSecondary, padding: 16 },
//     title: { color: colors.onSurfaceSecondary, fontSize: 16 },
//   }));
//   function Screen() {
//     const styles = useStyles();
//     return <View style={styles.card}><Text style={styles.title}>Hi</Text></View>;
//   }
// For color props that are not styles (icon color, placeholderTextColor,
// ActivityIndicator) read useTheme().colors inside the component.
// Never call StyleSheet.create with color values at module level; it cannot
// follow the scheme.
//
// To support dark mode later: add `dark` to `themes` with every key filled.
// Nothing else changes; the device setting takes over automatically.
// Feel free to add as many new colors as you need to support the design guidelines.

import { useMemo } from "react";
import { Appearance, StyleSheet, useColorScheme } from "react-native";

export type ColorScheme = "light" | "dark";

// ORDO's warm, restrained B2B palette. Navigation deliberately uses the
// inverse surface while operational content stays light and calm.
const light = {
  surface: "#FFFFFF",
  onSurface: "#17211B",
  surfaceSecondary: "#F5F3EE",
  onSurfaceSecondary: "#28332C",
  surfaceTertiary: "#EEECE6",
  onSurfaceTertiary: "#3D4941",
  surfaceInverse: "#17261F",
  onSurfaceInverse: "#F7F5EF",
  inverseMuted: "#AEBBB3",
  inverseSubtle: "#87958C",
  inverseLabel: "#75837A",
  inverseBorder: "#314037",
  inverseActive: "#2B3C32",
  inverseAccent: "#C5A889",
  muted: "#69746D",

  brand: "#17261F",
  onBrand: "#FFFFFF",
  brandPrimary: "#8A5A34",
  onBrandPrimary: "#FFFFFF",
  brandSecondary: "#46715B",
  onBrandSecondary: "#FFFFFF",
  brandTertiary: "#F0E5D9",
  onBrandTertiary: "#694326",

  success: "#059669",
  onSuccess: "#FFFFFF",
  warning: "#D97706",
  onWarning: "#FFFFFF",
  error: "#DC2626",
  onError: "#FFFFFF",
  info: "#2563EB",
  onInfo: "#FFFFFF",

  border: "#E2DED5",
  borderStrong: "#C8C2B7",
  divider: "#ECE8E0",
};

export type ThemeColors = typeof light;

export const defaultScheme = "light" satisfies ColorScheme;

export const themes: { light: ThemeColors; dark?: ThemeColors } = { light };

export const tokens = {
  spacing: { xxs: 4, xs: 8, sm: 12, md: 16, lg: 20, xl: 28, xxl: 36 },
  radius: { xs: 6, sm: 9, md: 12, lg: 16, pill: 999 },
  control: { button: 44, input: 46, touch: 44 },
  typography: {
    display: 34,
    title: 26,
    section: 17,
    body: 14,
    caption: 12,
  },
  layout: { sidebar: 264, content: 1320, narrow: 840, desktop: 1024, tablet: 720 },
  shadow: {
    shadowColor: "#17211B",
    shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 0.06,
    shadowRadius: 16,
    elevation: 2,
  },
} as const;

// In-app theme toggle, only after `dark` exists in `themes`. Call
// setColorScheme("dark"), setColorScheme("light"), or setColorScheme(null) to
// follow the device. Every useTheme() consumer re-renders. Persisting the
// choice and re-applying it on launch is the toggle's job.
export function setColorScheme(scheme: ColorScheme | null) {
  Appearance.setColorScheme?.(scheme ?? "unspecified");
}

// Keep native surfaces (alerts, pickers, navigation chrome) on the schemes this
// app ships: light only forces light; once `dark` exists the device decides.
// Optional call because react-native-web does not implement it.
setColorScheme?.(themes.dark ? null : defaultScheme);

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const system = useColorScheme();
  const scheme: ColorScheme = system === "dark" && themes.dark ? "dark" : defaultScheme;
  return { scheme, colors: themes[scheme] ?? themes.light };
}

// Themed StyleSheet: returns a hook that builds the sheet from the active
// scheme's colors and memoizes it until the scheme changes.
export function makeStyles<T extends StyleSheet.NamedStyles<T> | StyleSheet.NamedStyles<any>>(
  factory: (colors: ThemeColors) => T & StyleSheet.NamedStyles<any>,
): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}
