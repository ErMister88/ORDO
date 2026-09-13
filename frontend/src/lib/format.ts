export const euro = (v: number) =>
  new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(v || 0);

export const num = (v: number, digits = 0) =>
  new Intl.NumberFormat("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v || 0);

export const dateDE = (v: string) => {
  try {
    return new Intl.DateTimeFormat("de-DE").format(new Date(v));
  } catch {
    return v;
  }
};
