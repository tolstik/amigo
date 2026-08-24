package ru.tolstik.amigo.sync.xiaomi

import java.io.IOException
import java.security.MessageDigest
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import ru.tolstik.amigo.sync.network.IngestApi
import ru.tolstik.amigo.sync.sync.ExportRecord
import ru.tolstik.amigo.sync.sync.INGEST_BODY_LIMIT_BYTES
import ru.tolstik.amigo.sync.wire.CanonicalJson

internal class XiaomiSyncCoordinator(
    private val credentialsStore: XiaomiCredentialStore,
    private val preferences: XiaomiSyncPreferences,
    private val ingest: IngestApi,
    private val http: okhttp3.OkHttpClient,
    private val clock: Clock = Clock.systemUTC(),
    private val historyFloor: Instant = Instant.parse("2000-01-01T00:00:00Z"),
) {
    suspend fun enableFromSealedSession(sealed: String): XiaomiSyncSummary {
        credentialsStore.saveSealed(sealed)
        val credentials = credentialsStore.load() ?: error("Сессия Xiaomi недоступна")
        preferences.enable(credentials.accountFingerprint, credentials.region)
        return sync(maxPages = 4)
    }

    suspend fun disable() {
        val credentials = credentialsStore.load()
        if (credentials != null && preferences.enabled()) {
            val response = ingest.reportMiFitnessStatus(
                XiaomiStatusReport(
                    reportId = UUID.randomUUID().toString(),
                    enabled = false,
                    status = "disabled",
                ),
            )
            check(!response.enabled && !response.active)
        }
        preferences.disable()
        credentialsStore.clear()
    }

    suspend fun sync(maxPages: Int, refreshDays: Long = 3): XiaomiSyncSummary {
        require(maxPages in 1..40)
        if (!preferences.enabled()) {
            return XiaomiSyncSummary(0, 0, active = false, needsContinuation = false)
        }
        var credentials = credentialsStore.load() ?: run {
            preferences.setServerState("auth_required", active = preferences.status(false).active, "missing_session")
            throw XiaomiCloudException.AuthRequired()
        }
        try {
            // A failed first status request must not leave local Xiaomi state enabled while
            // the server still rejects every batch as mi_fitness_not_enabled. Reassert the
            // bounded source status before any provider fetch or batch upload on every run.
            report("pending", credentials)
            if (!preferences.regionDiscoveredFor(credentials.accountFingerprint)) {
                credentials = discoverRegionWithOneRefresh(credentials)
            }
            var remaining = maxPages
            var uploaded = 0
            var firstFailure: Exception? = null
            var index = preferences.nextMetricIndex()
            var visited = 0
            while (remaining > 0 && visited < XiaomiMetric.entries.size) {
                val metric = XiaomiMetric.entries[index]
                try {
                    val result = syncOnePage(metric, credentials, refreshDays)
                    uploaded += result
                    remaining -= 1
                } catch (error: CancellationException) {
                    throw error
                } catch (error: XiaomiCloudException.AuthRequired) {
                    credentials = refreshOnce(credentials)
                    try {
                        uploaded += syncOnePage(metric, credentials, refreshDays)
                        remaining -= 1
                    } catch (second: XiaomiCloudException.AuthRequired) {
                        report("auth_required", credentials, "auth_required")
                        throw second
                    }
                } catch (error: Exception) {
                    if (firstFailure == null) firstFailure = error
                }
                index = (index + 1) % XiaomiMetric.entries.size
                preferences.setNextMetricIndex(index)
                visited += 1
            }
            if (firstFailure != null) {
                val code = xiaomiSyncErrorCode(firstFailure)
                report(
                    if (firstFailure is XiaomiCloudException.RateLimited) "rate_limited" else "network_error",
                    credentials,
                    code,
                )
                throw firstFailure
            }
            val initialReady = XiaomiMetric.entries.all { preferences.historyEnd(it) != null }
            val response = report(if (initialReady) "success" else "pending", credentials)
            if (initialReady) preferences.markSuccess(clock.instant(), response.active)
            val complete = XiaomiMetric.entries.count {
                preferences.historyEnd(it)?.let { end -> end <= historyFloor } == true
            }
            val hasCursor = XiaomiMetric.entries.any { preferences.cursor(it) != null }
            return XiaomiSyncSummary(
                uploadedBatches = uploaded,
                completedTypes = complete,
                active = response.active,
                needsContinuation = hasCursor || complete < XiaomiMetric.entries.size,
            )
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            if (error is XiaomiCloudException.AuthRequired) {
                runCatching { report("auth_required", credentials, "auth_required") }
            } else {
                val current = preferences.status(credentialsStore.hasCredentials())
                if (current.status == "pending" || current.status == "success") {
                    preferences.setServerState("network_error", current.active, "sync_failed")
                }
            }
            throw error
        }
    }

    private suspend fun syncOnePage(
        metric: XiaomiMetric,
        credentials: XiaomiCredentials,
        refreshDays: Long,
    ): Int {
        val cursor = preferences.cursor(metric) ?: newCursor(metric, refreshDays)
        val client = XiaomiCloudClient(credentials, http)
        val page = if (metric == XiaomiMetric.EXERCISE) {
            client.sportPage(cursor.rangeStart, cursor.rangeEnd, cursor.nextKey)
        } else if (
            preferences.historyEnd(metric) == null &&
            metric in setOf(
                XiaomiMetric.RESTING_HEART_RATE,
                XiaomiMetric.SLEEP,
                XiaomiMetric.HRV_RMSSD,
            )
        ) {
            client.latestData(requireNotNull(metric.cloudKey), limit = 30)
        } else {
            client.dataPage(
                cloudKey = requireNotNull(metric.cloudKey),
                from = cursor.rangeStart,
                until = cursor.rangeEnd,
                nextKey = cursor.nextKey,
            )
        }
        val records = XiaomiParsers.records(metric, page.entries, cursor.rangeStart, cursor.rangeEnd)
        val sourceDataAsOf = listOfNotNull(cursor.sourceDataAsOf, page.sourceDataAsOf).maxOrNull()
        val envelopes = XiaomiBatchPlanner.plan(
            metric = metric,
            records = records,
            rangeStart = cursor.rangeStart,
            rangeEnd = cursor.rangeEnd,
            snapshotId = cursor.snapshotId,
            firstPageIndex = cursor.pageIndex,
            sourceFinalPage = page.nextKey == null,
            sourceDataAsOf = sourceDataAsOf,
            now = clock.instant(),
        )
        envelopes.forEach { ingest.uploadMiFitness(it) }
        if (page.nextKey == null) {
            preferences.completeWindow(metric, cursor.rangeStart, sourceDataAsOf)
        } else {
            preferences.setCursor(
                metric,
                cursor.copy(
                    nextKey = page.nextKey,
                    pageIndex = cursor.pageIndex + envelopes.size,
                    sourceDataAsOf = sourceDataAsOf,
                ),
            )
        }
        return envelopes.size
    }

    private fun newCursor(metric: XiaomiMetric, refreshDays: Long): XiaomiCursor {
        val now = clock.instant()
        val historyEnd = preferences.historyEnd(metric)
        val rangeEnd: Instant
        val rangeStart: Instant
        when {
            historyEnd == null -> {
                rangeEnd = now
                rangeStart = now.minus(Duration.ofDays(3))
            }
            historyEnd > historyFloor -> {
                rangeEnd = historyEnd
                rangeStart = maxOf(historyFloor, historyEnd.minus(Duration.ofDays(30)))
            }
            else -> {
                rangeEnd = now
                rangeStart = now.minus(Duration.ofDays(refreshDays.coerceIn(3, 30)))
            }
        }
        return XiaomiCursor(
            snapshotId = "mi-${metric.type.wireName}-${UUID.randomUUID()}",
            rangeStart = rangeStart,
            rangeEnd = rangeEnd,
        ).also { preferences.setCursor(metric, it) }
    }

    private suspend fun discoverRegionWithOneRefresh(
        credentials: XiaomiCredentials,
    ): XiaomiCredentials {
        return try {
            discoverRegion(credentials)
        } catch (_: XiaomiCloudException.AuthRequired) {
            discoverRegion(refreshOnce(credentials))
        }
    }

    private suspend fun discoverRegion(credentials: XiaomiCredentials): XiaomiCredentials = coroutineScope {
        val probes = XIAOMI_REGIONS.map { region ->
            async {
                try {
                    val page = XiaomiCloudClient(credentials, http).latestProbe(region)
                    XiaomiRegionProbe(region, reachable = true, authRequired = false, page.sourceDataAsOf)
                } catch (_: XiaomiCloudException.AuthRequired) {
                    XiaomiRegionProbe(region, reachable = false, authRequired = true, latest = null)
                } catch (_: Exception) {
                    XiaomiRegionProbe(region, reachable = false, authRequired = false, latest = null)
                }
            }
        }.awaitAll()
        val winner = chooseXiaomiRegion(probes, credentials.region)
        val updated = credentials.copy(region = winner)
        credentialsStore.save(updated)
        preferences.markRegionDiscovered(credentials.accountFingerprint, winner)
        updated
    }

    private suspend fun refreshOnce(credentials: XiaomiCredentials): XiaomiCredentials {
        val refreshed = XiaomiPassportClient(http).refresh(credentials)
        credentialsStore.save(refreshed)
        return refreshed
    }

    private suspend fun report(
        status: String,
        credentials: XiaomiCredentials,
        errorCode: String? = null,
    ) = ingest.reportMiFitnessStatus(
        XiaomiStatusReport(
            reportId = UUID.randomUUID().toString(),
            enabled = true,
            status = status,
            accountFingerprint = credentials.accountFingerprint,
            region = credentials.region,
            dataAsOf = preferences.dataAsOf(),
            errorCode = errorCode,
        ),
    ).also { response ->
        preferences.setServerState(response.status, response.active, errorCode)
    }
}

private val SAFE_SERVER_REJECTION =
    Regex("^Amigo server returned HTTP [45]\\d\\d \\(([a-z][a-z0-9_]{0,63})\\)$")

internal fun xiaomiSyncErrorCode(error: Exception): String = when (error) {
    is XiaomiCloudException.RateLimited -> "rate_limited"
    is XiaomiCloudException.Network -> "network_error"
    else -> error.message
        ?.let(SAFE_SERVER_REJECTION::matchEntire)
        ?.groupValues
        ?.get(1)
        ?: "invalid_cloud_response"
}

internal data class XiaomiRegionProbe(
    val region: String,
    val reachable: Boolean,
    val authRequired: Boolean,
    val latest: Instant?,
)

internal fun chooseXiaomiRegion(probes: List<XiaomiRegionProbe>, previousRegion: String): String {
    val reachable = probes.filter(XiaomiRegionProbe::reachable)
    if (reachable.isEmpty()) {
        if (probes.any(XiaomiRegionProbe::authRequired)) throw XiaomiCloudException.AuthRequired()
        throw XiaomiCloudException.Network(IOException("No Xiaomi region responded"))
    }
    val withData = reachable.filter { it.latest != null }
    return if (withData.isNotEmpty()) {
        withData.maxWith(
            compareBy<XiaomiRegionProbe> { it.latest }
                .thenBy { -XIAOMI_REGIONS.indexOf(it.region) },
        ).region
    } else {
        reachable.firstOrNull { it.region == previousRegion }?.region ?: reachable.first().region
    }
}

internal object XiaomiBatchPlanner {
    private const val MAX_RECORDS = 2_000

    fun plan(
        metric: XiaomiMetric,
        records: List<ExportRecord>,
        rangeStart: Instant,
        rangeEnd: Instant,
        snapshotId: String,
        firstPageIndex: Int,
        sourceFinalPage: Boolean,
        sourceDataAsOf: Instant?,
        now: Instant,
    ): List<XiaomiBatchEnvelope> {
        val chunks = if (records.isEmpty()) listOf(emptyList()) else split(records) { chunk, index ->
            envelope(
                metric, chunk, rangeStart, rangeEnd, snapshotId, firstPageIndex + index,
                false, sourceDataAsOf, now,
            )
        }
        return chunks.mapIndexed { index, chunk ->
            envelope(
                metric = metric,
                records = chunk,
                rangeStart = rangeStart,
                rangeEnd = rangeEnd,
                snapshotId = snapshotId,
                pageIndex = firstPageIndex + index,
                finalPage = sourceFinalPage && index == chunks.lastIndex,
                sourceDataAsOf = sourceDataAsOf,
                now = now,
            )
        }
    }

    private fun split(
        records: List<ExportRecord>,
        candidate: (List<ExportRecord>, Int) -> XiaomiBatchEnvelope,
    ): List<List<ExportRecord>> {
        val result = mutableListOf<List<ExportRecord>>()
        var current = mutableListOf<ExportRecord>()
        records.sortedBy(ExportRecord::recordId).forEach { record ->
            val attempted = current + record
            val tooMany = attempted.size > MAX_RECORDS
            val tooLarge = !tooMany && CanonicalJson.encode(candidate(attempted, result.size).toJson()).size >=
                INGEST_BODY_LIMIT_BYTES
            if (tooMany || tooLarge) {
                check(current.isNotEmpty()) { "One Xiaomi record exceeds the ingest limit" }
                result += current
                current = mutableListOf(record)
                check(CanonicalJson.encode(candidate(current, result.size).toJson()).size < INGEST_BODY_LIMIT_BYTES) {
                    "One Xiaomi record exceeds the ingest limit"
                }
            } else {
                current.add(record)
            }
        }
        if (current.isNotEmpty()) result += current
        return result
    }

    private fun envelope(
        metric: XiaomiMetric,
        records: List<ExportRecord>,
        rangeStart: Instant,
        rangeEnd: Instant,
        snapshotId: String,
        pageIndex: Int,
        finalPage: Boolean,
        sourceDataAsOf: Instant?,
        now: Instant,
    ): XiaomiBatchEnvelope {
        val requestKey = listOf(
            snapshotId,
            pageIndex.toString(),
            records.joinToString(",", transform = ExportRecord::recordId),
        ).joinToString("|")
        val batchId = "mi-" + MessageDigest.getInstance("SHA-256")
            .digest(requestKey.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
        return XiaomiBatchEnvelope(
            batchId = batchId,
            recordType = metric.type,
            dataAsOf = now,
            sourceDataAsOf = sourceDataAsOf,
            rangeStart = rangeStart,
            rangeEnd = rangeEnd,
            snapshotId = snapshotId,
            pageIndex = pageIndex,
            finalPage = finalPage,
            records = records,
        )
    }
}
