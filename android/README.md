# Amigo Android app

Hybrid Android app (`ru.tolstik.amigo.sync`) with the authenticated Amigo
dashboard as its default tab and native synchronization as its second tab. The
sync client reads Mi Fitness directly from Xiaomi Health Cloud, retains Health
Connect as rollback history, and sends only normalized signed/idempotent batches
to the Amigo server. It never requests write access, weight, blood pressure,
location, or exercise routes.

Current signed release `1.5.1` (`versionCode 18`) for project release
[`v5.3.1`](https://github.com/tolstik/amigo/releases/tag/v5.3.1):
[`Amigo-1.5.1.apk`](https://github.com/tolstik/amigo/releases/download/v5.3.1/Amigo-1.5.1.apk),
SHA-256
`0d171cdcc49a7e340f42bbc2c2c0f6fa4c095d1c70ce47eb1413b28f95a40f44`, size
`3,530,505` bytes.
The signing-certificate SHA-256 is
`25:CC:38:EC:B3:10:81:F6:82:6F:F0:49:B8:07:33:5A:05:E8:6E:E9:89:54:70:97:5E:85:21:AF:95:19:1C:02`.

Version `1.5.1` separates recent three-day synchronization from monthly
reconciliation and increases each run's budget to 40 provider pages, starting
no new page after 90 seconds. Deploy the accompanying server coverage selector
before installing the new APK: an older monthly snapshot must not overwrite a
newer recent snapshot just because its final page arrived later.

The previous published release is `1.5.0` from
[`v5.3.0`](https://github.com/tolstik/amigo/releases/tag/v5.3.0):
[`Amigo-1.5.0.apk`](https://github.com/tolstik/amigo/releases/download/v5.3.0/Amigo-1.5.0.apk),
SHA-256 `4ac0cf4035eb8b5b29df30de0c2bbe6b78c2d4e1caef1ee7fc348e994922ce2c`,
size `3,520,750` bytes.

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
- Handles only exact authenticated CSV, laboratory-original, and doctor-report
  PDF download routes. A doctor PDF must be an exact same-origin `GET` to
  `/amigo/api/v1/reports/doctor/<canonical-lowercase-UUID>.pdf`; query strings,
  fragments, malformed IDs, other methods, and redirects are rejected.
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

To sign the release, provide the keystore path, alias, and both passwords
through the build environment (do not put them in Gradle files, shell history,
or the repository):

- `AMIGO_ANDROID_KEYSTORE` — absolute path to the release keystore;
- `AMIGO_ANDROID_KEYSTORE_PASSWORD` or the preferred root-only
  `AMIGO_ANDROID_KEYSTORE_PASSWORD_FILE`;
- `AMIGO_ANDROID_KEY_ALIAS`;
- `AMIGO_ANDROID_KEY_PASSWORD` or the preferred root-only
  `AMIGO_ANDROID_KEY_PASSWORD_FILE`.

Then run `./gradlew assembleRelease`. With all four inputs present, Gradle uses
that signing configuration; without them, the release stays unsigned.

Install or update the debug build over USB with:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## First setup

1. Open Amigo. The dashboard is the default tab; sign in with the existing
   local Amigo account. Open **Синхронизация** for Health Connect setup.
2. Keep the default server `https://amigo.tolstik.ru`, enter a phone label, and
   register the device.
3. Approve the displayed pairing code on the server, then tap **Проверить**.
4. Tap **Войти в Xiaomi**. The password is entered only on Xiaomi's HTTPS page
   in a separate WebView process. Amigo retains the resulting session only as
   AES-GCM ciphertext protected by a non-exportable Android Keystore key; no
   Xiaomi credential, cookie, token, account ID, or provider payload is sent to
   the Amigo server. Email-code fields use the system keyboard, and the
   in-progress Xiaomi form is preserved while switching to the mail app.
5. Optionally grant the requested read permissions in Health Connect, tap
   **Найти источники**, and explicitly select Mi Fitness. These signed records
   remain rollback history. In particular, Health Connect steps never become
   published dashboard steps; only finalized Xiaomi Cloud steps are eligible
   after the direct-source activation gate succeeds.
6. Tap **Синхронизировать сейчас**. WorkManager continues best-effort sync about
   once an hour when networking and background Health Connect reads are
   available. It also starts one immediate run when the app process is created
   and schedules a one-minute continuation while the bounded backfill remains
   incomplete. The screen shows the last background start, result, and finish.

The direct-cloud activation window is fixed by the first qualifying recent
three-day snapshot in the current enablement episode. All ten allowlisted types
must finalize coverage for that same window, even when bounded pagination takes
longer than the normal freshness tolerance, and cloud heart rate must be newer
than the retained Health Connect watermark. The hourly three-day refresh,
weekly 30-day reconciliation, and descending historical backfill have independent
resumable lanes. Each reconciliation round keeps one immutable target and width
for all ten metrics; manual or weekly requests never move an unfinished round.
Old 3/30-day refresh cursors finish in their existing lane, preserving their
snapshot IDs, provider page state and record hashes. The new recent lane can
finish fresh steps before those old snapshots finish. Scheduling offers eight
recent pages, one monthly page, and one historical page per ten attempts, using
otherwise idle shares for available work. The per-lane metric order and shares
survive process restarts. Multiple pages of a metric may finish in one bounded
run, and delayed continuations check recent freshness again when Android wakes.
Historical cursors still descend to `2000-01-01`. Signed upload pacing remains
at least 1.1 seconds. The screen shows the last completed steps range separately
from the source's aggregate freshness and indicates unfinished step downloads.
If Xiaomi Cloud is not fresher, it remains
pending instead of silently replacing Health Connect. The independent Health
Connect backfill still skips provider-confirmed empty prefixes and uses its
existing bounded snapshot/changes-token contract.

**Сбросить сопряжение** deletes and immediately replaces the non-exportable
Android Keystore key before clearing local pairing and sync cursors. Use it only
when a new server registration is intended; the replacement device must be
approved with its new pairing code.

## Wire contract

- Registration: `POST /amigo-ingest/v1/devices/register`.
- Pairing status: `GET /amigo-ingest/v1/devices/{device_id}/status`.
- Health batches: `POST /amigo-ingest/v1/health-connect/batches`.
- Xiaomi Cloud snapshots: `POST /amigo-ingest/v1/mi-fitness/batches`.
- Xiaomi source status: `POST /amigo-ingest/v1/mi-fitness/status`.

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
health payload or private key is written to logs. Release 1.3.2 prefers a
record's Health Connect modification timestamp over a future interval end when
forming the freshness watermark. It also continues later record types after an
earlier type fails, then reports the first safe allowlisted rejection and keeps
the bounded WorkManager retry. This prevents current Mi Fitness step/calorie
intervals from blocking sleep in the same run. On first start after upgrade it
replaces only the heart-rate changes token and performs a full reconciliation
for that record type. Pairing, the non-exportable key, selected origin, and all
other type tokens/cursors remain unchanged. It retains the earlier worker,
run-ID, DNS, and empty-cursor behavior.

Xiaomi Cloud snapshots use the same signed headers and limits. Every Xiaomi
sync first reasserts the signed server source status before any cloud fetch or
batch upload. This repairs an interrupted initial enablement without clearing
the encrypted credentials, pairing state, or cursors. A safe allowlisted server
rejection such as `mi_fitness_not_enabled` remains visible instead of being
reported as a generic cloud-response error. Xiaomi batch retries use the
persisted range end as `data_as_of`, and `mi-v2` IDs bind the full canonical
normalized content. While a snapshot is unfinished, its cursor persists at most
20,000 SHA-256 record-ID hashes and filters overlap from later provider pages.
A legacy batch/sequence conflict rotates only that metric's unfinished snapshot
and retries it exactly once; credentials, pairing, completed history, and other
metric cursors are unchanged. The phone parses
only steps/distance, active calories, exercise summaries, sleep, hourly
min/average/max/count heart rate, dedicated resting heart rate, overnight HRV,
SpO2, and VO2 max. Raw heart-rate samples and provider JSON never leave the
phone. Partial cloud pages stay invisible; a final page atomically publishes
coverage, and confirmed-empty coverage suppresses Health Connect only for that
metric/range. Identical replay/reconciliation is a structural no-op and does not
request another AI analysis. Authentication expiry remains visible and sends a
deduplicated server alert; explicit logout disables cloud precedence and clears
the encrypted local session.

## Pool swimming in 1.5.0

Version 1.5.0 (code 17) adds `/amigo/swimming` to the exact dashboard allowlist
and removes laboratory panel comparison. The exercise importer recognizes the
verified Xiaomi pool sport type, uploads bounded optional swimming summaries,
and rereads exercise history once after upgrade without resetting pairing or
other metric cursors. See [the swimming contract](../docs/xiaomi-swimming.md)
for field provenance, units and unavailable-field handling.
