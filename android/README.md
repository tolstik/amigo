# Amigo Android app

Hybrid Android app (`ru.tolstik.amigo.sync`) with the authenticated Amigo
dashboard as its default tab and native Health Connect synchronization as its
second tab. The sync client reads Mi Fitness data and sends signed, idempotent
batches to the Amigo server. It never requests write access, weight, blood
pressure, location, or exercise routes.

Current signed release `1.2.3` (`versionCode 8`) for project release
[`v5.0.4`](https://github.com/tolstik/amigo/releases/tag/v5.0.4):
[`Amigo-1.2.3.apk`](https://github.com/tolstik/amigo/releases/download/v5.0.4/Amigo-1.2.3.apk),
SHA-256
`f57cf09e1dd71c219ff7206ad0507310cf77a545fd976350a166df0b69c69e70`.
The signing-certificate SHA-256 is
`25:CC:38:EC:B3:10:81:F6:82:6F:F0:49:B8:07:33:5A:05:E8:6E:E9:89:54:70:97:5E:85:21:AF:95:19:1C:02`.

The previous published release is `1.2.2`:
[`Amigo-1.2.2.apk`](https://github.com/tolstik/amigo/releases/download/v5.0.3/Amigo-1.2.2.apk),
SHA-256 `4c8168013d49439072c0a084ea3284d88916d0164b5fba47201c60861ee9454a`.

## Dashboard tab

- Loads only `https://amigo.tolstik.ru/amigo/` and known dashboard routes in a
  top-level WebView; verified links for `/amigo` and `/amigo/...` open this tab.
- Uses the normal username/password form and persistent first-party WebView
  cookie. Web login/logout never changes the independent ingest pairing.
- Enables JavaScript and DOM storage required by the React dashboard, but has no
  JavaScript bridge and rejects third-party cookies, mixed content, TLS errors,
  external origins, unsafe schemes, and unknown main-frame routes.
- Uses Android Files/Photos to choose up to 25 PDF/JPG/PNG/HEIC/HEIF files, each
  up to 20 MiB.
  Camera capture is intentionally not requested.
- Handles only exact authenticated CSV and laboratory-original download routes.
  The session cookie stays in memory, redirects are disabled, the response is
  capped at 25 MiB, and the destination is selected through system “Save as”.
- Keeps no offline medical archive. A failed connection or renderer shows a
  native retry screen.
- Reloads a dashboard that returns to the foreground after at least 30 seconds,
  so server-side processing and background synchronization become visible
  without a forced refresh.

## In-app update

The synchronization tab exposes **Проверить обновление**. It reads authenticated
metadata from the exact production origin, downloads only
`/amigo/api/v1/app-update/apk`, caps the file at 150 MiB, and verifies exact
length and SHA-256. It then verifies package `ru.tolstik.amigo.sync`, a strictly
higher version code, and the same signing-certificate set as the installed app.
Only after every check passes is the APK handed to the Android system installer;
the user must still explicitly confirm installation. Redirects and alternate
origins are rejected.

## Build and test

Requirements: JDK 17 or newer and Android SDK Platform 36. `ANDROID_HOME` must
point to the SDK. The checked-in `gradlew` bootstraps the pinned Gradle 8.13
distribution and verifies its SHA-256 checksum.

```bash
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

The installable debug APK is written to
`app/build/outputs/apk/debug/app-debug.apk`.

An unsigned optimized release is built with:

```bash
./gradlew assembleRelease
```

To sign the release, provide all four values through the build environment (do
not put them in Gradle files, shell history, or the repository):

- `AMIGO_ANDROID_KEYSTORE` — absolute path to the release keystore;
- `AMIGO_ANDROID_KEYSTORE_PASSWORD`;
- `AMIGO_ANDROID_KEY_ALIAS`;
- `AMIGO_ANDROID_KEY_PASSWORD`.

Then run `./gradlew assembleRelease`. With all four values present, Gradle uses
that signing configuration; without them, the release stays unsigned.

Install or update the debug build over USB with:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## First setup

1. Open Amigo. The dashboard is the default tab; sign in with the existing
   local Amigo account. Open **Синхронизация** for Health Connect setup.
2. Grant the requested read permissions. Grant history and background access
   when Health Connect exposes those capabilities.
3. Tap **Найти источники** and explicitly select Mi Fitness. The source package
   is discovered from record metadata and is not hard-coded.
4. Keep the default server `https://amigo.tolstik.ru`, enter a phone label, and
   register the device.
5. Approve the displayed pairing code on the server, then tap **Проверить**.
6. Tap **Синхронизировать сейчас**. WorkManager continues best-effort sync about
   once an hour when networking and background Health Connect reads are
   available. It also starts one immediate run when the app process is created
   and schedules a one-minute continuation while the bounded backfill remains
   incomplete. The screen shows the last background start, result, and finish.

The full backfill is resumable and may require several runs. A newly paired
device skips the provider-confirmed empty prefix and starts at the first real
record; token-expiry reconciliation still scans bounded 30-day windows so
deletions remain detectable. “Full history” means all records Health Connect
allows this app to read. On providers without extended-history support, the app
safely starts 30 days before its first observed permission grant.

**Сбросить сопряжение** deletes and immediately replaces the non-exportable
Android Keystore key before clearing local pairing and sync cursors. Use it only
when a new server registration is intended; the replacement device must be
approved with its new pairing code.

## Wire contract

- Registration: `POST /amigo-ingest/v1/devices/register`.
- Pairing status: `GET /amigo-ingest/v1/devices/{device_id}/status`.
- Health batches: `POST /amigo-ingest/v1/health-connect/batches`.

The app generates a non-exportable P-256 key in Android Keystore. Every batch
uses the headers `X-Amigo-Device-Id`, `X-Amigo-Timestamp`, `X-Amigo-Nonce`,
`X-Amigo-Batch-Id`, and `X-Amigo-Signature`. The DER ECDSA/SHA-256 signature is
standard Base64 over these exact bytes:

```text
timestamp + "\n" + nonce + "\n" + batch_id + "\n" + raw_json_body
```

Changes tokens are independent per record type and advance only after server
acknowledgement. Initial and token-expiry reconciliation uses paginated snapshot
windows; the final page lets the server tombstone records no longer present in
Health Connect. Windows containing records are at most 30 days. A
provider-confirmed empty prefix or gap is sent as one empty final snapshot,
capped at 50 years, so old anomalous records do not force years of monthly
requests. Canonical batches are
strictly smaller than 1,048,576 bytes, contain at most 2,000 records, and cap a
heart-rate record at 5,000 evenly sampled points while preserving the first and
last point. Batch starts are separated by at least 1,100 ms to remain below the
production 60 requests/minute limit. A failed upload leaves the cursor/token
unchanged, so the same deterministic batch ID and body are retried. No raw
health payload or private key is written to logs. Release 1.2.3 prefers a
record's Health Connect modification timestamp over a future interval end when
forming the freshness watermark. It also continues later record types after an
earlier type fails, then reports the first safe allowlisted rejection and keeps
the bounded WorkManager retry. This prevents current Mi Fitness step/calorie
intervals from blocking sleep in the same run. It retains the 1.2.2 worker,
run-ID, DNS, and empty-cursor behavior. Installing it over 1.2.2 preserves the
pairing key, selected origin, changes tokens, and resumable sync cursors.
