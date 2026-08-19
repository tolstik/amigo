package ru.tolstik.amigo.sync.sync

import java.io.IOException
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.tolstik.amigo.sync.wire.CanonicalJson

class SyncCoordinatorTest {
    private val floor = Instant.parse("2026-01-01T00:00:00Z")
    private val now = Instant.parse("2026-01-02T00:00:00Z")
    private val origin = "com.example.health"

    @Test
    fun createsTokenBeforePaginatedBackfillAndMarksOnlyFinalPage() = runTest {
        val source = FakeHealthSource(
            earliest = floor,
            snapshotPages = mutableMapOf(
                null to SnapshotPage(listOf(record("one")), "page-2"),
                "page-2" to SnapshotPage(listOf(record("two")), null),
            ),
        )
        val state = FakeState(origin)
        val uploader = FakeUploader()
        val coordinator = coordinator(source, uploader, state)

        val summary = coordinator.syncAll(maxPagesPerType = 4)

        assertTrue(source.events.indexOf("token") < source.events.indexOf("earliest"))
        assertEquals(listOf(false, true), uploader.batches.map { it.finalPage })
        assertEquals(listOf(0, 1), uploader.batches.map { it.pageIndex })
        assertEquals(2, summary.uploadedBatches)
        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals("next-token", state.changesToken(RecordType.STEPS))
        assertNotNull(state.lastSyncValue)
    }

    @Test
    fun failedUploadDoesNotAdvanceCursorAndRetryUsesSameBatchId() = runTest {
        val source = FakeHealthSource(
            earliest = floor,
            snapshotPages = mutableMapOf(null to SnapshotPage(listOf(record("one")), null)),
        )
        val state = FakeState(origin)
        val uploader = FakeUploader(failuresRemaining = 1)
        val coordinator = coordinator(source, uploader, state)

        runCatching { coordinator.syncAll(maxPagesPerType = 1) }
            .onSuccess { error("Expected upload failure") }
        val cursorAfterFailure = state.snapshotCursor(RecordType.STEPS)
        assertEquals(0, cursorAfterFailure?.pageIndex)
        assertNull(cursorAfterFailure?.pageToken)
        assertFalse(state.isSnapshotComplete(RecordType.STEPS))

        coordinator.syncAll(maxPagesPerType = 1)

        assertEquals(2, uploader.attemptedBatchIds.size)
        assertEquals(uploader.attemptedBatchIds[0], uploader.attemptedBatchIds[1])
        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
    }

    @Test
    fun expiredChangesTokenCreatesNewTokenThenRunsFullEmptyReconcile() = runTest {
        val source = FakeHealthSource(earliest = null, expireOldToken = true)
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "old-token")
            markSnapshotComplete(RecordType.STEPS)
        }
        val uploader = FakeUploader()
        val coordinator = coordinator(source, uploader, state)

        coordinator.syncAll(maxPagesPerType = 3)

        assertEquals(listOf("changes:old-token", "token", "earliest", "changes:fresh-token"), source.events)
        assertEquals(1, uploader.batches.size)
        val reconcile = uploader.batches.single()
        assertEquals(BatchMode.SNAPSHOT, reconcile.mode)
        assertTrue(reconcile.records.isEmpty())
        assertEquals(floor, reconcile.rangeStart)
        assertEquals(now, reconcile.rangeEnd)
        assertTrue(reconcile.finalPage == true)
        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
    }

    @Test
    fun deletionOnlyRetryKeepsBatchIdAndRawBodyStable() = runTest {
        val source = FakeHealthSource(
            earliest = floor,
            changes = listOf(ExportChange.Delete("deleted-record")),
        )
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "delete-token")
            markSnapshotComplete(RecordType.STEPS)
        }
        val uploader = FakeUploader(failuresRemaining = 1)

        runCatching { coordinator(source, uploader, state).syncAll(maxPagesPerType = 1) }
        coordinator(source, uploader, state).syncAll(maxPagesPerType = 1)

        val first = uploader.attemptedBatches[0]
        val second = uploader.attemptedBatches[1]
        assertEquals(first.batchId, second.batchId)
        assertEquals(Instant.EPOCH, first.dataAsOf)
        assertArrayEquals(
            CanonicalJson.encode(first.toJson()),
            CanonicalJson.encode(second.toJson()),
        )
    }

    @Test
    fun emptyHistoryUsesResumableThirtyDaySnapshotWindows() = runTest {
        val longFloor = Instant.parse("2026-01-01T00:00:00Z")
        val longNow = Instant.parse("2026-05-15T00:00:00Z")
        val source = FakeHealthSource(earliest = null)
        val state = FakeState(origin)
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = Clock.fixed(longNow, ZoneOffset.UTC),
            generationIds = GenerationIds { "empty-generation" },
            historyFloor = longFloor,
        )

        coordinator.syncAll(maxPagesPerType = 2)

        assertFalse(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals(2, uploader.batches.size)
        assertTrue(uploader.batches.all { batch ->
            Duration.between(batch.rangeStart, batch.rangeEnd) <= Duration.ofDays(30)
        })
        assertTrue(uploader.batches.all { it.records.isEmpty() && it.finalPage == true })
        assertTrue(source.events.none { it.startsWith("snapshot:") })

        coordinator.syncAll(maxPagesPerType = 8)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals(longNow, uploader.batches.last().rangeEnd)
        assertTrue(uploader.batches.all { batch ->
            Duration.between(batch.rangeStart, batch.rangeEnd) <= Duration.ofDays(30)
        })
    }

    @Test
    fun realisticOversizedHeartRatePageIsDeterministicallyBounded() {
        val records = List(20) { index ->
            heartRecord(
                id = "heart-${index.toString().padStart(2, '0')}",
                sampleCount = if (index == 0) 6_000 else 1_500,
            )
        }
        val planner = BatchPlanner()
        val snapshotId = "snapshot-oversized"
        val batches = planner.snapshot(
            type = RecordType.HEART_RATE,
            origin = origin,
            sourceRecords = records,
            rangeStart = floor,
            rangeEnd = now,
            snapshotId = snapshotId,
            firstPageIndex = 0,
            sourceFinalPage = true,
        )

        assertTrue(batches.size > 1)
        assertEquals(batches.indices.toList(), batches.map { it.pageIndex })
        assertTrue(batches.dropLast(1).all { it.finalPage == false })
        assertTrue(batches.last().finalPage == true)
        assertTrue(batches.all { planner.encodedSize(it) < INGEST_BODY_LIMIT_BYTES })
        assertTrue(batches.all { it.records.size <= MAX_RECORDS_PER_BATCH })

        val bounded = batches.flatMap(BatchEnvelope::records).first { it.recordId == "heart-00" }
        val samples = bounded.values.getValue("samples").jsonArray
        assertEquals(MAX_HEART_RATE_SAMPLES, samples.size)
        assertEquals(
            records.first().values.getValue("samples").jsonArray.first(),
            samples.first(),
        )
        assertEquals(
            records.first().values.getValue("samples").jsonArray.last(),
            samples.last(),
        )
    }

    @Test
    fun partialSnapshotBatchFailureReplaysStableChunksBeforeAdvancingCursor() = runTest {
        val source = FakeHealthSource(
            earliest = floor,
            snapshotPages = mutableMapOf(
                null to SnapshotPage(List(5) { record("record-$it") }, null),
            ),
        )
        val state = FakeState(origin)
        val uploader = FakeUploader(failOnAttempts = mutableSetOf(2))
        val coordinator = coordinator(
            source,
            uploader,
            state,
            batchPlanner = BatchPlanner(maxRecordsPerBatch = 2),
        )

        runCatching { coordinator.syncAll(maxPagesPerType = 1) }
            .onSuccess { error("Expected upload failure") }

        assertEquals(0, state.snapshotCursor(RecordType.STEPS)?.pageIndex)
        assertFalse(state.isSnapshotComplete(RecordType.STEPS))

        coordinator.syncAll(maxPagesPerType = 1)

        assertEquals(uploader.attemptedBatches[0].batchId, uploader.attemptedBatches[2].batchId)
        assertArrayEquals(
            CanonicalJson.encode(uploader.attemptedBatches[0].toJson()),
            CanonicalJson.encode(uploader.attemptedBatches[2].toJson()),
        )
        assertEquals(uploader.attemptedBatches[1].batchId, uploader.attemptedBatches[3].batchId)
        assertArrayEquals(
            CanonicalJson.encode(uploader.attemptedBatches[1].toJson()),
            CanonicalJson.encode(uploader.attemptedBatches[3].toJson()),
        )
        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
    }

    @Test
    fun partialChangesBatchFailureDoesNotAdvanceTokenAndRetriesStableChunks() = runTest {
        val source = FakeHealthSource(
            earliest = floor,
            changes = List(5) { ExportChange.Upsert(record("change-$it")) },
        )
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "changes-token")
            markSnapshotComplete(RecordType.STEPS)
        }
        val uploader = FakeUploader(failOnAttempts = mutableSetOf(2))
        val coordinator = coordinator(
            source,
            uploader,
            state,
            batchPlanner = BatchPlanner(maxRecordsPerBatch = 2),
        )

        runCatching { coordinator.syncAll(maxPagesPerType = 1) }
            .onSuccess { error("Expected upload failure") }

        assertEquals("changes-token", state.changesToken(RecordType.STEPS))

        coordinator.syncAll(maxPagesPerType = 1)

        assertEquals("next-token", state.changesToken(RecordType.STEPS))
        assertEquals(uploader.attemptedBatches[0].batchId, uploader.attemptedBatches[2].batchId)
        assertArrayEquals(
            CanonicalJson.encode(uploader.attemptedBatches[0].toJson()),
            CanonicalJson.encode(uploader.attemptedBatches[2].toJson()),
        )
        assertEquals(uploader.attemptedBatches[1].batchId, uploader.attemptedBatches[3].batchId)
    }

    private fun coordinator(
        source: FakeHealthSource,
        uploader: FakeUploader,
        state: FakeState,
        batchPlanner: BatchPlanner = BatchPlanner(),
    ) = SyncCoordinator(
        source = source,
        uploader = uploader,
        state = state,
        clock = Clock.fixed(now, ZoneOffset.UTC),
        generationIds = GenerationIds { "generation-1" },
        historyFloor = floor,
        batchPlanner = batchPlanner,
    )

    private fun record(id: String) = ExportRecord(
        recordId = id,
        type = RecordType.STEPS,
        startTime = floor.plusSeconds(60),
        endTime = floor.plusSeconds(120),
        dataOrigin = origin,
        lastModifiedTime = floor.plusSeconds(180),
        values = buildJsonObject { put("count", 100) },
    )

    private fun heartRecord(id: String, sampleCount: Int) = ExportRecord(
        recordId = id,
        type = RecordType.HEART_RATE,
        startTime = floor,
        endTime = floor.plusSeconds(sampleCount.toLong()),
        dataOrigin = origin,
        lastModifiedTime = now,
        values = buildJsonObject {
            put(
                "samples",
                JsonArray(List(sampleCount) { index ->
                    buildJsonObject {
                        put("beats_per_minute", 60 + index % 90)
                        put("time", floor.plusSeconds(index.toLong()).toString())
                    }
                }),
            )
        },
    )

    private class FakeHealthSource(
        private val earliest: Instant?,
        private val snapshotPages: MutableMap<String?, SnapshotPage> = mutableMapOf(),
        private val expireOldToken: Boolean = false,
        private val changes: List<ExportChange> = emptyList(),
    ) : HealthDataSource {
        val events = mutableListOf<String>()

        override suspend fun enabledTypes() = listOf(RecordType.STEPS)

        override suspend fun findEarliest(
            type: RecordType,
            dataOrigin: String,
            from: Instant,
            until: Instant,
        ): Instant? {
            events += "earliest"
            return earliest
        }

        override suspend fun readSnapshotPage(
            type: RecordType,
            dataOrigin: String,
            from: Instant,
            until: Instant,
            pageToken: String?,
        ): SnapshotPage {
            events += "snapshot:${pageToken ?: "first"}"
            return snapshotPages[pageToken] ?: SnapshotPage(emptyList(), null)
        }

        override suspend fun createChangesToken(type: RecordType, dataOrigin: String): String {
            events += "token"
            return if (events.any { it == "changes:old-token" }) "fresh-token" else "initial-token"
        }

        override suspend fun readChanges(
            type: RecordType,
            dataOrigin: String,
            token: String,
        ): ChangePage {
            events += "changes:$token"
            if (expireOldToken && token == "old-token") {
                return ChangePage(emptyList(), token, false, true)
            }
            return ChangePage(changes, "next-token", false, false)
        }
    }

    private class FakeUploader(
        var failuresRemaining: Int = 0,
        private val failOnAttempts: MutableSet<Int> = mutableSetOf(),
    ) : BatchUploader {
        val batches = mutableListOf<BatchEnvelope>()
        val attemptedBatches = mutableListOf<BatchEnvelope>()
        val attemptedBatchIds = mutableListOf<String>()

        override suspend fun upload(batch: BatchEnvelope) {
            attemptedBatches += batch
            attemptedBatchIds += batch.batchId
            val attempt = attemptedBatches.size
            if (attempt in failOnAttempts || failuresRemaining > 0) {
                if (failuresRemaining > 0) failuresRemaining -= 1
                throw IOException("offline")
            }
            batches += batch
        }
    }

    private class FakeState(private val origin: String) : SyncStateStore {
        private val tokens = mutableMapOf<RecordType, String>()
        private val cursors = mutableMapOf<RecordType, SnapshotCursor>()
        private val complete = mutableSetOf<RecordType>()
        var asOf: Instant? = null
        var lastSyncValue: Instant? = null
        var lastErrorValue: String? = null

        override fun selectedOrigin() = origin
        override fun changesToken(type: RecordType) = tokens[type]
        override fun setChangesToken(type: RecordType, token: String) { tokens[type] = token }
        override fun snapshotCursor(type: RecordType) = cursors[type]
        override fun setSnapshotCursor(type: RecordType, cursor: SnapshotCursor) {
            cursors[type] = cursor
            complete -= type
        }
        override fun isSnapshotComplete(type: RecordType) = type in complete
        override fun markSnapshotComplete(type: RecordType) {
            complete += type
            cursors -= type
        }
        override fun resetSnapshot(type: RecordType) {
            complete -= type
            cursors -= type
        }
        override fun setDataAsOf(value: Instant) {
            if (asOf == null || value > asOf) asOf = value
        }
        override fun dataAsOf() = asOf
        override fun setLastSync(value: Instant) { lastSyncValue = value }
        override fun setLastError(value: String?) { lastErrorValue = value }
    }
}
