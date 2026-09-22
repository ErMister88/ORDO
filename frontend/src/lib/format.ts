export const money = (v: number, currency = "EUR") =>
  new Intl.NumberFormat("de-DE", { style: "currency", currency }).format(v || 0);

export const euro = (v: number) => money(v, "EUR");

export const moneyMinor = (minor: number, currency = "EUR") => money(minor / 100, currency);

export const num = (v: number, digits = 0) =>
  new Intl.NumberFormat("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v || 0);

export const dateDE = (v: string) => {
  try {
    return new Intl.DateTimeFormat("de-DE").format(new Date(v));
  } catch {
    return v;
  }
};
