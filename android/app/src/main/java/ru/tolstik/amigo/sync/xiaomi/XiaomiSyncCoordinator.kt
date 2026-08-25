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
        return sync(maxPages = 4, mode = XiaomiSyncMode.FORCE_REFRESH)
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

    suspend fun sync(
        maxPages: Int,
        refreshDays: Long = 3,
        mode: XiaomiSyncMode = XiaomiSyncMode.ROUTINE,
    ): XiaomiSyncSummary {
        require(maxPages in 1..40)
        if (!preferences.enabled()) {
            return XiaomiSyncSummary(0, 0, active = false, needsContinuation = false)
        }
        val requestedRefreshTarget = clock.instant()
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
            // Persist one immutable target before fetching any metric. All ten dedicated
            // refresh cursors are then materialised up front, so bounded continuations resume
            // this round without either moving its activation window or starving backfill.
            val refreshRound = prepareRefreshRound(
                requestedTarget = requestedRefreshTarget,
                requestedDays = refreshDays,
                mode = mode,
            )
            var remaining = maxPages
            var uploaded = 0
            var firstFailure: Exception? = null
            var index = preferences.nextMetricIndex()
            var visited = 0
            while (remaining > 0 && visited < XiaomiMetric.entries.size) {
                val metric = XiaomiMetric.entries[index]
                val lane = nextLane(metric)
                if (lane != null) {
                    try {
                        val result = syncOnePageWithRecovery(
                            metric,
                            lane,
                            credentials,
                        )
                        uploaded += result
                        remaining -= 1
                    } catch (error: CancellationException) {
                        throw error
                    } catch (error: XiaomiCloudException.AuthRequired) {
                        credentials = refreshOnce(credentials)
                        try {
                            uploaded += syncOnePageWithRecovery(
                                metric,
                                lane,
                                credentials,
                            )
                            remaining -= 1
                        } catch (second: XiaomiCloudException.AuthRequired) {
                            report("auth_required", credentials, "auth_required")
                            throw second
                        }
                    } catch (error: Exception) {
                        if (firstFailure == null) firstFailure = error
                    }
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
            val refreshPending = refreshRound?.let { round ->
                XiaomiMetric.entries.any { metric ->
                    preferences.refreshCursor(metric) != null ||
                        !xiaomiRefreshCovers(
                            preferences.refreshStart(metric),
                            preferences.refreshEnd(metric),
                            round,
                        )
                }
            } == true
            if (refreshRound != null && !refreshPending) {
                preferences.clearRefreshRound(refreshRound)
            }
            val historyPending = XiaomiMetric.entries.any {
                preferences.historyCursor(it) != null ||
                    preferences.historyEnd(it)?.let { end -> end > historyFloor } != false
            }
            return XiaomiSyncSummary(
                uploadedBatches = uploaded,
                completedTypes = complete,
                active = response.active,
                needsContinuation = refreshPending || historyPending,
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

    private suspend fun syncOnePageWithRecovery(
        metric: XiaomiMetric,
        lane: XiaomiCursorLane,
        credentials: XiaomiCredentials,
    ): Int = try {
        syncOnePage(metric, lane, credentials)
    } catch (error: CancellationException) {
        throw error
    } catch (error: Exception) {
        if (!shouldRestartXiaomiSnapshot(error)) throw error
        val cursor = cursor(metric, lane) ?: throw error
        setCursor(
            metric,
            lane,
            restartXiaomiCursor(cursor, freshSnapshotId(metric)),
        )
        // Exactly one targeted retry. A second conflict is surfaced to the normal bounded
        // worker retry path instead of creating an unbounded provider/server loop. The other
        // lane remains byte-for-byte intact.
        syncOnePage(metric, lane, credentials)
    }

    private suspend fun syncOnePage(
        metric: XiaomiMetric,
        lane: XiaomiCursorLane,
        credentials: XiaomiCredentials,
    ): Int {
        val cursor = cursor(metric, lane) ?: when (lane) {
            XiaomiCursorLane.REFRESH -> error("Xiaomi refresh cursor disappeared")
            XiaomiCursorLane.HISTORY -> newHistoryCursor(metric)
        }
        val client = XiaomiCloudClient(credentials, http)
        val page = if (metric == XiaomiMetric.EXERCISE) {
            client.sportPage(cursor.rangeStart, cursor.rangeEnd, cursor.nextKey)
        } else if (
            lane == XiaomiCursorLane.REFRESH &&
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
        val parsedRecords = XiaomiParsers.records(metric, page.entries, cursor.rangeStart, cursor.rangeEnd)
        val records = unseenXiaomiRecords(parsedRecords, cursor.seenRecordHashes)
        val sourceDataAsOf = listOfNotNull(cursor.sourceDataAsOf, page.sourceDataAsOf).maxOrNull()
        val nextSeenRecordHashes = if (page.nextKey == null) {
            emptySet()
        } else {
            (cursor.seenRecordHashes + parsedRecords.map { xiaomiRecordHash(it.recordId) })
                .also { require(it.size <= MAX_XIAOMI_SEEN_RECORD_HASHES) }
        }
        val envelopes = XiaomiBatchPlanner.plan(
            metric = metric,
            records = records,
            rangeStart = cursor.rangeStart,
            rangeEnd = cursor.rangeEnd,
            snapshotId = cursor.snapshotId,
            firstPageIndex = cursor.pageIndex,
            sourceFinalPage = page.nextKey == null,
            sourceDataAsOf = sourceDataAsOf,
        )
        envelopes.forEach { ingest.uploadMiFitness(it) }
        if (page.nextKey == null) {
            when (lane) {
                XiaomiCursorLane.REFRESH -> preferences.completeRefreshWindow(
                    metric,
                    cursor.rangeStart,
                    cursor.rangeEnd,
                    sourceDataAsOf,
                )
                XiaomiCursorLane.HISTORY -> preferences.completeHistoryWindow(
                    metric,
                    cursor.rangeStart,
                    sourceDataAsOf,
                )
            }
        } else {
            setCursor(
                metric,
                lane,
                cursor.copy(
                    nextKey = page.nextKey,
                    pageIndex = cursor.pageIndex + envelopes.size,
                    sourceDataAsOf = sourceDataAsOf,
                    seenRecordHashes = nextSeenRecordHashes,
                ),
            )
        }
        return envelopes.size
    }

    private fun newHistoryCursor(metric: XiaomiMetric): XiaomiCursor {
        val rangeEnd = requireNotNull(preferences.historyEnd(metric))
        val rangeStart = maxOf(historyFloor, rangeEnd.minus(Duration.ofDays(30)))
        return XiaomiCursor(
            snapshotId = freshSnapshotId(metric),
            rangeStart = rangeStart,
            rangeEnd = rangeEnd,
        ).also { preferences.setHistoryCursor(metric, it) }
    }

    private fun prepareRefreshRound(
        requestedTarget: Instant,
        requestedDays: Long,
        mode: XiaomiSyncMode,
    ): XiaomiRefreshRound? {
        val boundedDays = requestedDays.coerceIn(3, 30)
        val inheritedCursor = XiaomiMetric.entries
            .firstNotNullOfOrNull(preferences::refreshCursor)
        var round = preferences.refreshRound()

        if (round == null && inheritedCursor != null) {
            // Adopt a resumable pre-round cursor written by an older client. Creating the
            // remaining per-metric cursors from its exact target keeps the activation window
            // stable instead of silently restarting the provider page chain.
            round = XiaomiRefreshRound(
                target = inheritedCursor.rangeEnd,
                days = Duration.between(
                    inheritedCursor.rangeStart,
                    inheritedCursor.rangeEnd,
                ).toDays().coerceIn(3, 30),
            )
        }

        val initialRoundMissing = XiaomiMetric.entries.any {
            preferences.historyEnd(it) == null
        }
        val routineRoundDue = mode != XiaomiSyncMode.BACKFILL_CONTINUATION &&
            XiaomiMetric.entries.any { metric ->
                shouldStartXiaomiRefresh(
                    lastRangeStart = preferences.refreshStart(metric),
                    lastRangeEnd = preferences.refreshEnd(metric),
                    target = requestedTarget,
                    refreshDays = boundedDays,
                    mode = mode,
                )
            }
        if (round == null && (initialRoundMissing || routineRoundDue)) {
            round = XiaomiRefreshRound(requestedTarget, boundedDays)
        } else if (
            round != null &&
            mode != XiaomiSyncMode.BACKFILL_CONTINUATION &&
            (boundedDays > round.days ||
                (mode == XiaomiSyncMode.FORCE_REFRESH && requestedTarget > round.target))
        ) {
            // A broader weekly request, or an explicit manual request, supersedes the round.
            // Existing page cursors still finish exactly; their metric is then reconciled once
            // more against this persisted target instead of being reset mid-snapshot.
            round = XiaomiRefreshRound(requestedTarget, maxOf(round.days, boundedDays))
        }

        val activeRound = round ?: return null
        preferences.setRefreshRound(activeRound)
        XiaomiMetric.entries.forEach { metric ->
            if (
                preferences.refreshCursor(metric) == null &&
                !xiaomiRefreshCovers(
                    preferences.refreshStart(metric),
                    preferences.refreshEnd(metric),
                    activeRound,
                )
            ) {
                preferences.setRefreshCursor(
                    metric,
                    XiaomiCursor(
                        snapshotId = freshSnapshotId(metric),
                        rangeStart = activeRound.target.minus(Duration.ofDays(activeRound.days)),
                        rangeEnd = activeRound.target,
                    ),
                )
            }
        }
        return activeRound
    }

    private fun nextLane(metric: XiaomiMetric): XiaomiCursorLane? = selectXiaomiCursorLane(
        hasRefreshCursor = preferences.refreshCursor(metric) != null,
        hasHistoryCursor = preferences.historyCursor(metric) != null,
        historyEnd = preferences.historyEnd(metric),
        historyFloor = historyFloor,
    )

    private fun cursor(metric: XiaomiMetric, lane: XiaomiCursorLane): XiaomiCursor? = when (lane) {
        XiaomiCursorLane.REFRESH -> preferences.refreshCursor(metric)
        XiaomiCursorLane.HISTORY -> preferences.historyCursor(metric)
    }

    private fun setCursor(metric: XiaomiMetric, lane: XiaomiCursorLane, cursor: XiaomiCursor) {
        when (lane) {
            XiaomiCursorLane.REFRESH -> preferences.setRefreshCursor(metric, cursor)
            XiaomiCursorLane.HISTORY -> preferences.setHistoryCursor(metric, cursor)
        }
    }

    private fun freshSnapshotId(metric: XiaomiMetric) =
        "mi-${metric.type.wireName}-${UUID.randomUUID()}"

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

private val XIAOMI_ROUTINE_REFRESH_INTERVAL = Duration.ofHours(1)

internal fun xiaomiRefreshCovers(
    rangeStart: Instant?,
    rangeEnd: Instant?,
    round: XiaomiRefreshRound,
): Boolean = rangeStart != null &&
    rangeEnd != null &&
    rangeStart <= round.target.minus(Duration.ofDays(round.days)) &&
    rangeEnd >= round.target

internal fun selectXiaomiCursorLane(
    hasRefreshCursor: Boolean,
    hasHistoryCursor: Boolean,
    historyEnd: Instant?,
    historyFloor: Instant,
): XiaomiCursorLane? = when {
    hasRefreshCursor -> XiaomiCursorLane.REFRESH
    hasHistoryCursor -> XiaomiCursorLane.HISTORY
    historyEnd?.let { it > historyFloor } == true -> XiaomiCursorLane.HISTORY
    else -> null
}

internal fun shouldStartXiaomiRefresh(
    lastRangeStart: Instant?,
    lastRangeEnd: Instant?,
    target: Instant,
    refreshDays: Long,
    mode: XiaomiSyncMode,
): Boolean {
    if (lastRangeStart == null || lastRangeEnd == null) return true
    val requestedStart = target.minus(Duration.ofDays(refreshDays.coerceIn(3, 30)))
    val previousWindowTooNarrow = lastRangeStart > requestedStart
    return when (mode) {
        XiaomiSyncMode.BACKFILL_CONTINUATION -> false
        XiaomiSyncMode.FORCE_REFRESH -> lastRangeEnd < target || previousWindowTooNarrow
        XiaomiSyncMode.ROUTINE ->
            lastRangeEnd <= target.minus(XIAOMI_ROUTINE_REFRESH_INTERVAL) ||
                previousWindowTooNarrow
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

private val RESTARTABLE_XIAOMI_SNAPSHOT_CODES = setOf(
    "batch_id_conflict",
    "snapshot_already_finalised",
    "snapshot_metadata_conflict",
    "snapshot_page_out_of_order",
    "snapshot_pages_incomplete",
    "snapshot_record_repeated",
)

internal fun shouldRestartXiaomiSnapshot(error: Exception): Boolean =
    xiaomiSyncErrorCode(error) in RESTARTABLE_XIAOMI_SNAPSHOT_CODES

internal fun restartXiaomiCursor(cursor: XiaomiCursor, snapshotId: String) = cursor.copy(
    snapshotId = snapshotId,
    nextKey = null,
    pageIndex = 0,
    sourceDataAsOf = null,
    seenRecordHashes = emptySet(),
)

internal fun xiaomiRecordHash(recordId: String): String = MessageDigest.getInstance("SHA-256")
    .digest(recordId.toByteArray(Charsets.UTF_8))
    .joinToString("") { "%02x".format(it) }

internal fun unseenXiaomiRecords(
    records: List<ExportRecord>,
    seenRecordHashes: Set<String>,
): List<ExportRecord> = records.filterNot { record ->
    xiaomiRecordHash(record.recordId) in seenRecordHashes
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
    ): List<XiaomiBatchEnvelope> {
        val chunks = if (records.isEmpty()) listOf(emptyList()) else split(records) { chunk, index ->
            envelope(
                metric, chunk, rangeStart, rangeEnd, snapshotId, firstPageIndex + index,
                false, sourceDataAsOf,
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
    ): XiaomiBatchEnvelope {
        val unsigned = XiaomiBatchEnvelope(
            batchId = "",
            recordType = metric.type,
            dataAsOf = rangeEnd,
            sourceDataAsOf = sourceDataAsOf,
            rangeStart = rangeStart,
            rangeEnd = rangeEnd,
            snapshotId = snapshotId,
            pageIndex = pageIndex,
            finalPage = finalPage,
            records = records,
        )
        val batchId = "mi-v2-" + MessageDigest.getInstance("SHA-256")
            .digest(CanonicalJson.encode(unsigned.identityJson()))
            .joinToString("") { "%02x".format(it) }
        return unsigned.copy(batchId = batchId)
    }
}
