package ru.tolstik.amigo.sync.sync

import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.UUID

interface HealthDataSource {
    suspend fun enabledTypes(): List<RecordType> = RecordType.entries

    suspend fun accessibleHistoryFloor(requestedFloor: Instant, asOf: Instant): Instant =
        requestedFloor

    suspend fun findEarliest(
        type: RecordType,
        dataOrigin: String,
        from: Instant,
        until: Instant,
    ): Instant?

    suspend fun readSnapshotPage(
        type: RecordType,
        dataOrigin: String,
        from: Instant,
        until: Instant,
        pageToken: String?,
    ): SnapshotPage

    suspend fun createChangesToken(type: RecordType, dataOrigin: String): String

    suspend fun readChanges(type: RecordType, dataOrigin: String, token: String): ChangePage
}

interface BatchUploader {
    suspend fun upload(batch: BatchEnvelope)
}

interface SyncStateStore {
    fun selectedOrigin(): String?

    fun changesToken(type: RecordType): String?

    fun setChangesToken(type: RecordType, token: String)

    fun snapshotCursor(type: RecordType): SnapshotCursor?

    fun setSnapshotCursor(type: RecordType, cursor: SnapshotCursor)

    fun isSnapshotComplete(type: RecordType): Boolean

    fun markSnapshotComplete(type: RecordType)

    fun resetSnapshot(type: RecordType)

    fun setDataAsOf(value: Instant)

    fun dataAsOf(): Instant?

    fun setLastSync(value: Instant)

    fun setLastError(value: String?)
}

fun interface GenerationIds {
    fun next(): String
}

class SyncCoordinator(
    private val source: HealthDataSource,
    private val uploader: BatchUploader,
    private val state: SyncStateStore,
    private val clock: Clock = Clock.systemUTC(),
    private val generationIds: GenerationIds = GenerationIds { UUID.randomUUID().toString() },
    private val historyFloor: Instant = Instant.parse("2000-01-01T00:00:00Z"),
    private val snapshotWindow: Duration = Duration.ofDays(30),
    private val batchPlanner: BatchPlanner = BatchPlanner(),
) {
    suspend fun syncAll(maxPagesPerType: Int = 4): SyncSummary {
        require(maxPagesPerType > 0)
        val origin = state.selectedOrigin()?.takeIf(String::isNotBlank)
            ?: throw IllegalStateException("Health Connect source is not selected")
        val runTarget = clock.instant()
        val accessibleFloor = source.accessibleHistoryFloor(historyFloor, runTarget)
            .coerceAtLeast(historyFloor)
            .coerceAtMost(runTarget.minusMillis(1))
        val enabledTypes = source.enabledTypes()
        check(enabledTypes.isNotEmpty()) { "No Health Connect read permissions are granted" }
        var uploaded = 0
        try {
            for (type in enabledTypes) {
                uploaded += syncType(type, origin, runTarget, accessibleFloor, maxPagesPerType)
            }
            state.setLastSync(clock.instant())
            state.setLastError(null)
        } catch (error: Exception) {
            state.setLastError(error.message?.take(300) ?: error::class.java.simpleName)
            throw error
        }
        return SyncSummary(
            uploadedBatches = uploaded,
            completedTypes = enabledTypes.count(state::isSnapshotComplete),
            dataAsOf = state.dataAsOf(),
        )
    }

    private suspend fun syncType(
        type: RecordType,
        origin: String,
        runTarget: Instant,
        accessibleFloor: Instant,
        budget: Int,
    ): Int {
        state.snapshotCursor(type)?.let { cursor ->
            if (
                cursor.formatVersion != SNAPSHOT_CURSOR_FORMAT_VERSION ||
                cursor.rangeStart < accessibleFloor
            ) {
                state.resetSnapshot(type)
            }
        }
        ensureChangesToken(type, origin)
        var uploaded = 0
        var remaining = budget
        while (remaining > 0) {
            if (!state.isSnapshotComplete(type)) {
                uploaded += syncSnapshotPage(type, origin, runTarget, accessibleFloor)
                remaining -= 1
                continue
            }
            when (val result = syncChangesPage(type, origin)) {
                ChangeStep.Idle -> return uploaded
                ChangeStep.Reset -> continue
                is ChangeStep.Uploaded -> {
                    uploaded += result.batchCount
                    remaining -= 1
                }
                ChangeStep.Advanced -> remaining -= 1
            }
        }
        return uploaded
    }

    private suspend fun ensureChangesToken(type: RecordType, origin: String) {
        if (state.changesToken(type) != null) return
        state.setChangesToken(type, source.createChangesToken(type, origin))
    }

    private suspend fun syncSnapshotPage(
        type: RecordType,
        origin: String,
        runTarget: Instant,
        accessibleFloor: Instant,
    ): Int {
        val cursor = state.snapshotCursor(type)
            ?: initialiseSnapshot(type, origin, runTarget, accessibleFloor)
        val page = if (cursor.knownEmpty) {
            SnapshotPage(emptyList(), null)
        } else {
            source.readSnapshotPage(
                type = type,
                dataOrigin = origin,
                from = cursor.rangeStart,
                until = cursor.rangeEnd,
                pageToken = cursor.pageToken,
            )
        }
        val sourceFinalPage = page.nextPageToken == null
        val snapshotId = stableId(
            "snapshot-v2",
            cursor.generation,
            type.wireName,
            cursor.rangeStart.toString(),
            cursor.rangeEnd.toString(),
        )
        val batches = batchPlanner.snapshot(
            type = type,
            origin = origin,
            sourceRecords = page.records,
            rangeStart = cursor.rangeStart,
            rangeEnd = cursor.rangeEnd,
            snapshotId = snapshotId,
            firstPageIndex = cursor.pageIndex,
            sourceFinalPage = sourceFinalPage,
        )
        batches.forEach { batch ->
            uploader.upload(batch)
            state.setDataAsOf(batch.dataAsOf)
        }
        if (!sourceFinalPage) {
            state.setSnapshotCursor(
                type,
                cursor.copy(
                    pageToken = page.nextPageToken,
                    pageIndex = cursor.pageIndex + batches.size,
                ),
            )
        } else {
            advanceSnapshot(type, cursor)
        }
        return batches.size
    }

    private suspend fun initialiseSnapshot(
        type: RecordType,
        origin: String,
        target: Instant,
        accessibleFloor: Instant,
    ): SnapshotCursor {
        val earliest = source.findEarliest(type, origin, accessibleFloor, target)
            ?.coerceAtLeast(accessibleFloor)
            ?.coerceAtMost(target)
        val emptyUntil = when {
            earliest == null -> target
            earliest > accessibleFloor -> earliest
            else -> null
        }
        val rangeEnd = minOf(
            accessibleFloor.plus(snapshotWindow),
            target,
            emptyUntil ?: target,
        )
        val cursor = SnapshotCursor(
            generation = generationIds.next(),
            target = target,
            rangeStart = accessibleFloor,
            rangeEnd = rangeEnd,
            knownEmpty = emptyUntil != null,
            emptyUntil = emptyUntil,
        )
        state.setSnapshotCursor(type, cursor)
        return cursor
    }

    private fun advanceSnapshot(type: RecordType, cursor: SnapshotCursor) {
        if (cursor.rangeEnd >= cursor.target) {
            state.markSnapshotComplete(type)
            return
        }
        val nextStart = cursor.rangeEnd
        val emptyUntil = cursor.emptyUntil?.takeIf { nextStart < it }
        val nextEnd = minOf(
            nextStart.plus(snapshotWindow),
            cursor.target,
            emptyUntil ?: cursor.target,
        )
        state.setSnapshotCursor(
            type,
            cursor.copy(
                rangeStart = nextStart,
                rangeEnd = nextEnd,
                pageToken = null,
                pageIndex = 0,
                knownEmpty = emptyUntil != null,
                emptyUntil = emptyUntil,
            ),
        )
    }

    private suspend fun syncChangesPage(type: RecordType, origin: String): ChangeStep {
        val token = state.changesToken(type) ?: error("Changes token disappeared")
        val page = source.readChanges(type, origin, token)
        if (page.tokenExpired) {
            // Establish the new observation point before starting the full reconcile.
            state.setChangesToken(type, source.createChangesToken(type, origin))
            state.resetSnapshot(type)
            return ChangeStep.Reset
        }
        val records = page.changes.map { change ->
            when (change) {
                is ExportChange.Upsert -> change.record
                is ExportChange.Delete -> ExportRecord(
                    recordId = change.recordId,
                    type = type,
                    dataOrigin = origin,
                    deleted = true,
                )
            }
        }
        val batches = batchPlanner.changes(type, origin, token, records)
        batches.forEach { batch ->
            uploader.upload(batch)
            state.setDataAsOf(batch.dataAsOf)
        }
        state.setChangesToken(type, page.nextChangesToken)
        return when {
            batches.isNotEmpty() -> ChangeStep.Uploaded(batches.size)
            page.hasMore -> ChangeStep.Advanced
            else -> ChangeStep.Idle
        }
    }

    private sealed interface ChangeStep {
        data object Idle : ChangeStep
        data object Reset : ChangeStep
        data object Advanced : ChangeStep
        data class Uploaded(val batchCount: Int) : ChangeStep
    }
}

internal fun stableId(vararg parts: String): String {
    val digest = MessageDigest.getInstance("SHA-256")
    parts.forEachIndexed { index, part ->
        if (index > 0) digest.update(0.toByte())
        digest.update(part.toByteArray(StandardCharsets.UTF_8))
    }
    return digest.digest().joinToString("") { "%02x".format(it) }
}
