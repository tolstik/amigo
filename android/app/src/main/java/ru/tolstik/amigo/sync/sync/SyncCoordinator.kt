package ru.tolstik.amigo.sync.sync

import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException

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

internal fun userFacingSyncError(error: Throwable): String {
    val causes = generateSequence(error) { current -> current.cause }
    return when {
        causes.any { it is UnknownHostException } ->
            "Не удалось найти amigo.tolstik.ru. Проверьте интернет и настройки частного DNS; синхронизация повторится автоматически."
        causes.any { it is SocketTimeoutException } ->
            "Сервер не ответил вовремя. Синхронизация повторится автоматически."
        error is IOException ->
            "Не удалось связаться с сервером. Данные не потеряны, синхронизация повторится автоматически."
        else -> error.message?.take(300) ?: "Синхронизация завершилась с ошибкой"
    }
}

interface SyncStateStore {
    fun selectedOrigin(): String?

    fun changesToken(type: RecordType): String?

    fun setChangesToken(type: RecordType, token: String)

    fun snapshotCursor(type: RecordType): SnapshotCursor?

    fun setSnapshotCursor(type: RecordType, cursor: SnapshotCursor)

    fun isSnapshotComplete(type: RecordType): Boolean

    fun markSnapshotComplete(type: RecordType)

    fun snapshotRequiresFullReconcile(type: RecordType): Boolean

    fun snapshotTarget(type: RecordType): Instant?

    fun beginSnapshot(type: RecordType, target: Instant, requiresFullReconcile: Boolean)

    fun beginSnapshotWithChangesToken(
        type: RecordType,
        newToken: String,
        target: Instant,
        requiresFullReconcile: Boolean,
    )

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
        val enabledTypes = source.enabledTypes()
        check(enabledTypes.isNotEmpty()) { "No Health Connect read permissions are granted" }
        val legacyMigrationRequiresFullReconcile = enabledTypes.any(state::isSnapshotComplete)
        var uploaded = 0
        try {
            // Migrate every legacy cursor and persist every observation point before processing
            // pages. If a later type fails after an earlier one completes, a retry must not
            // reinterpret the remaining cursors using that newly completed type as evidence.
            for (type in enabledTypes) {
                prepareSnapshotObservation(
                    type,
                    origin,
                    legacyMigrationRequiresFullReconcile,
                )
            }
            for (type in enabledTypes) {
                uploaded += syncType(type, origin, maxPagesPerType)
            }
            state.setLastSync(clock.instant())
            state.setLastError(null)
        } catch (error: CancellationException) {
            // WorkManager may cancel a superseded request. Cancellation is lifecycle
            // control, not a Health Connect/server failure and must never become the
            // user-visible English "Job was cancelled" error.
            throw error
        } catch (error: Exception) {
            state.setLastError(userFacingSyncError(error))
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
        budget: Int,
    ): Int {
        var uploaded = 0
        var remaining = budget
        while (remaining > 0) {
            if (!state.isSnapshotComplete(type)) {
                uploaded += syncSnapshotPage(type, origin)
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

    private suspend fun prepareSnapshotObservation(
        type: RecordType,
        origin: String,
        legacyMigrationRequiresFullReconcile: Boolean,
    ) {
        val cursor = state.snapshotCursor(type)
        val explicitReconcile = state.snapshotRequiresFullReconcile(type)
        if (state.changesToken(type) == null) {
            val newToken = source.createChangesToken(type, origin)
            val target = clock.instant()
            state.beginSnapshotWithChangesToken(
                type = type,
                newToken = newToken,
                target = target,
                requiresFullReconcile = explicitReconcile ||
                    state.isSnapshotComplete(type) || cursor != null,
            )
            return
        }
        when {
            cursor?.formatVersion != null &&
                cursor.formatVersion != SNAPSHOT_CURSOR_FORMAT_VERSION -> {
                // v2 had no persisted purpose. A wholly unfinished installation is the known
                // initial-backfill case; otherwise prefer the safe full-reconcile migration.
                state.beginSnapshot(
                    type,
                    clock.instant(),
                    explicitReconcile || legacyMigrationRequiresFullReconcile,
                )
            }
            !state.isSnapshotComplete(type) && cursor == null && state.snapshotTarget(type) == null -> {
                // The token already predates this target, so overlap is safe and no record can fall
                // between the changes observation point and the snapshot boundary.
                state.beginSnapshot(type, clock.instant(), explicitReconcile)
            }
        }
    }

    private suspend fun syncSnapshotPage(
        type: RecordType,
        origin: String,
    ): Int {
        var cursor = state.snapshotCursor(type)
        var target = cursor?.target ?: state.snapshotTarget(type)
            ?: error("Snapshot observation target disappeared")
        var accessibleFloor = accessibleFloor(target)
        if (cursor != null && cursor.rangeStart < accessibleFloor) {
            // An existing, older token makes extending the target overlap-safe.
            target = clock.instant()
            state.beginSnapshot(type, target, requiresFullReconcile = true)
            cursor = null
            accessibleFloor = accessibleFloor(target)
        }
        val activeCursor = cursor
            ?: initialiseSnapshot(type, origin, target, accessibleFloor)
            ?: return 0
        val page = if (activeCursor.knownEmpty) {
            SnapshotPage(emptyList(), null)
        } else {
            source.readSnapshotPage(
                type = type,
                dataOrigin = origin,
                from = activeCursor.rangeStart,
                until = activeCursor.rangeEnd,
                pageToken = activeCursor.pageToken,
            )
        }
        val sourceFinalPage = page.nextPageToken == null
        val snapshotId = stableId(
            "snapshot-v3",
            activeCursor.generation,
            type.wireName,
            activeCursor.rangeStart.toString(),
            activeCursor.rangeEnd.toString(),
        )
        val batches = batchPlanner.snapshot(
            type = type,
            origin = origin,
            sourceRecords = page.records,
            rangeStart = activeCursor.rangeStart,
            rangeEnd = activeCursor.rangeEnd,
            snapshotId = snapshotId,
            firstPageIndex = activeCursor.pageIndex,
            sourceFinalPage = sourceFinalPage,
        )
        batches.forEach { batch ->
            uploader.upload(batch)
            state.setDataAsOf(batch.dataAsOf)
        }
        if (!sourceFinalPage) {
            state.setSnapshotCursor(
                type,
                activeCursor.copy(
                    pageToken = page.nextPageToken,
                    pageIndex = activeCursor.pageIndex + batches.size,
                ),
            )
        } else {
            advanceSnapshot(type, origin, activeCursor)
        }
        return batches.size
    }

    private suspend fun initialiseSnapshot(
        type: RecordType,
        origin: String,
        target: Instant,
        accessibleFloor: Instant,
    ): SnapshotCursor? {
        val earliest = source.findEarliest(type, origin, accessibleFloor, target)
            ?.coerceAtLeast(accessibleFloor)
            ?.coerceAtMost(target)
        if (!state.snapshotRequiresFullReconcile(type)) {
            if (earliest == null) {
                // A newly paired device has no server records to tombstone. Its initial import can
                // safely omit the provider-confirmed empty prefix (or the entire empty type).
                state.markSnapshotComplete(type)
                return null
            }
            val rangeStart = minOf(earliest, target.minusMillis(1))
            val cursor = SnapshotCursor(
                generation = generationIds.next(),
                target = target,
                rangeStart = rangeStart,
                rangeEnd = minOf(rangeStart.plus(snapshotWindow), target),
            )
            state.setSnapshotCursor(type, cursor)
            return cursor
        }
        val emptyUntil = when {
            earliest == null -> target
            earliest > accessibleFloor -> earliest
            else -> null
        }
        val rangeEnd = emptyUntil ?: minOf(accessibleFloor.plus(snapshotWindow), target)
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

    private suspend fun advanceSnapshot(
        type: RecordType,
        origin: String,
        cursor: SnapshotCursor,
    ) {
        if (cursor.rangeEnd >= cursor.target) {
            state.markSnapshotComplete(type)
            return
        }
        val nextStart = cursor.rangeEnd
        val persistedEmptyUntil = cursor.emptyUntil?.takeIf { nextStart < it }
        val beginsAtKnownRecord = cursor.emptyUntil == nextStart
        val earliest = when {
            persistedEmptyUntil != null -> null
            beginsAtKnownRecord -> nextStart
            else -> source.findEarliest(type, origin, nextStart, cursor.target)
                ?.coerceAtLeast(nextStart)
                ?.coerceAtMost(cursor.target)
        }
        val emptyUntil = persistedEmptyUntil ?: when {
            earliest == null -> cursor.target
            earliest > nextStart -> earliest
            else -> null
        }
        val nextEnd = emptyUntil ?: minOf(nextStart.plus(snapshotWindow), cursor.target)
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
            val newToken = source.createChangesToken(type, origin)
            state.beginSnapshotWithChangesToken(
                type = type,
                newToken = newToken,
                target = clock.instant(),
                requiresFullReconcile = true,
            )
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

    private suspend fun accessibleFloor(target: Instant): Instant =
        source.accessibleHistoryFloor(historyFloor, target)
            .coerceAtLeast(historyFloor)
            .coerceAtMost(target.minusMillis(1))
}

internal fun stableId(vararg parts: String): String {
    val digest = MessageDigest.getInstance("SHA-256")
    parts.forEachIndexed { index, part ->
        if (index > 0) digest.update(0.toByte())
        digest.update(part.toByteArray(StandardCharsets.UTF_8))
    }
    return digest.digest().joinToString("") { "%02x".format(it) }
}
