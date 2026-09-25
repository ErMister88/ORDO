import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Text as NativeText,
  type AlertButton,
  type AlertOptions,
  type TextProps,
} from "react-native";

import { storage } from "@/src/utils/storage";
import { extraTranslations } from "./translations-extra";
import { coreTranslations, SupportedLanguage, type Translation } from "./translations";
export type { SupportedLanguage } from "./translations";

export const translations: Record<string, Translation> = {
  ...coreTranslations,
  ...extraTranslations,
};

const LANGUAGE_STORAGE_KEY = "ordo_ui_language";
const locales: Record<SupportedLanguage, string> = {
  de: "de-DE",
  it: "it-IT",
  en: "en-GB",
};

let currentLanguage: SupportedLanguage = "de";

function isLanguage(value: unknown): value is SupportedLanguage {
  return value === "de" || value === "it" || value === "en";
}

export function getCurrentLanguage(): SupportedLanguage {
  return currentLanguage;
}

export function getCurrentLocale(): string {
  return locales[currentLanguage];
}

export function translate(value: string, language = currentLanguage): string {
  if (language === "de") return value;
  return translations[value]?.[language] ?? value;
}

function translateNode(node: React.ReactNode, language: SupportedLanguage): React.ReactNode {
  if (typeof node === "string") {
    const leading = node.match(/^\s*/)?.[0] ?? "";
    const trailing = node.match(/\s*$/)?.[0] ?? "";
    const end = trailing.length ? node.length - trailing.length : node.length;
    const content = node.slice(leading.length, end);
    return `${leading}${translate(content, language)}${trailing}`;
  }
  if (Array.isArray(node)) return node.map((child) => translateNode(child, language));
  return node;
}

type I18nContextValue = {
  language: SupportedLanguage;
  locale: string;
  setLanguage: (language: SupportedLanguage) => Promise<void>;
  t: (value: string) => string;
};

const I18nContext = createContext<I18nContextValue>({
  language: "de",
  locale: locales.de,
  setLanguage: async () => undefined,
  t: (value) => value,
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<SupportedLanguage>("de");

  useEffect(() => {
    let active = true;
    storage.getItem<string>(LANGUAGE_STORAGE_KEY, "de").then((stored) => {
      if (!active || !isLanguage(stored)) return;
      currentLanguage = stored;
      setLanguageState(stored);
    });
    return () => { active = false; };
  }, []);

  const setLanguage = useCallback(async (next: SupportedLanguage) => {
    if (!isLanguage(next)) return;
    currentLanguage = next;
    setLanguageState(next);
    await storage.setItem(LANGUAGE_STORAGE_KEY, next);
  }, []);

  const value = useMemo<I18nContextValue>(() => ({
    language,
    locale: locales[language],
    setLanguage,
    t: (text) => translate(text, language),
  }), [language, setLanguage]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}

/** Translates catalogued UI copy only. Business data is left unchanged. */
export function LocalizedText({ children, ...props }: TextProps) {
  const { language } = useI18n();
  return <NativeText {...props}>{translateNode(children, language)}</NativeText>;
}

export function localizedAlert(
  title: string,
  message?: string,
  buttons?: AlertButton[],
  options?: AlertOptions,
): void {
  Alert.alert(
    translate(title),
    message ? translate(message) : undefined,
    buttons?.map((button) => ({
      ...button,
      text: button.text ? translate(button.text) : button.text,
    })),
    options,
  );
}

export const languageLabels: Record<SupportedLanguage, string> = {
  de: "DE",
  it: "IT",
  en: "EN",
};
