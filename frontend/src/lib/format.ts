import { getCurrentLocale } from "@/src/i18n";

export const money = (v: number, currency = "EUR") =>
  new Intl.NumberFormat(getCurrentLocale(), { style: "currency", currency }).format(v || 0);

export const euro = (v: number) => money(v, "EUR");

export const moneyMinor = (minor: number, currency = "EUR") => money(minor / 100, currency);

export const num = (v: number, digits = 0) =>
  new Intl.NumberFormat(getCurrentLocale(), { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v || 0);

export const dateDE = (v: string) => {
  try {
    return new Intl.DateTimeFormat(getCurrentLocale()).format(new Date(v));
  } catch {
    return v;
  }
};
