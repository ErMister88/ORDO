const backendUrl = process.env.EXPO_PUBLIC_BACKEND_URL;

try {
  const url = new URL(backendUrl);
  const isLocalHttp =
    url.protocol === "http:" &&
    (url.hostname === "localhost" || url.hostname === "127.0.0.1");
  const isHttps = url.protocol === "https:";

  if (
    (!isHttps && !isLocalHttp) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/" ||
    backendUrl.endsWith("/")
  ) {
    throw new Error("invalid backend origin");
  }
} catch {
  console.error(
    "EXPO_PUBLIC_BACKEND_URL muss die HTTPS-Adresse des Backends ohne /api oder abschließenden Schrägstrich enthalten. Für lokale Tests ist HTTP auf localhost erlaubt.",
  );
  process.exit(1);
}
