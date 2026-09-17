# ORDO Frontend als Render Static Site

Das Frontend und das Backend werden als getrennte Render-Dienste betrieben. Für das Frontend ist eine **Static Site** mit diesen Einstellungen vorgesehen:

| Einstellung | Wert |
| --- | --- |
| Branch | `main` |
| Root Directory | `frontend` |
| Build Command | `yarn install --frozen-lockfile && yarn build:web` |
| Publish Directory | `dist` |

## Environment Variable

`EXPO_PUBLIC_BACKEND_URL` muss beim Build auf die öffentliche HTTPS-Adresse des Staging-Backends gesetzt werden, zum Beispiel `https://ordo-api-staging.onrender.com`.

Die Adresse darf weder `/api` noch einen abschließenden Schrägstrich enthalten. Variablen mit dem Präfix `EXPO_PUBLIC_` sind Bestandteil des öffentlichen Web-Bundles. Deshalb dürfen dort keine Secrets oder Zugangsdaten gespeichert werden.

## SPA Rewrite

Unter **Redirects/Rewrites** ist folgende Regel anzulegen, damit direkte Aufrufe und Browser-Neuladen auf Expo-Router-Seiten funktionieren:

| Source | Destination | Action |
| --- | --- | --- |
| `/*` | `/index.html` | `Rewrite` |

Ein Deployment wird mit diesen Dateien nicht ausgelöst. Nach Festlegung der Frontend-Domain muss sie separat in der erlaubten CORS-Konfiguration des Backends hinterlegt werden.
