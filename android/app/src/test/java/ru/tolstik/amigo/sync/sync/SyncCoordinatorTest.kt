package ru.tolstik.amigo.sync.sync

import java.io.IOException
import java.net.UnknownHostException
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset
import kotlinx.coroutines.CancellationException
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
    fun dnsFailureHasActionableRussianMessageWithoutLosingRetryMeaning() {
        val message = userFacingSyncError(
            IOException("network", UnknownHostException("amigo.tolstik.ru")),
        )

        assertTrue(message.contains("amigo.tolstik.ru"))
        assertTrue(message.contains("частного DNS"))
        assertTrue(message.contains("повторится автоматически"))
        assertFalse(message.contains("Unable to resolve host"))
    }

    @Test
    fun cancellationDoesNotBecomeAStoredSyncError() = runTest {
        val state = FakeState(origin)
        val source = FakeHealthSource(
            earliest = floor,
            onCreateToken = { throw CancellationException("Job was cancelled") },
        )

        val failure = runCatching {
            coordinator(source, FakeUploader(), state).syncAll(maxPagesPerType = 1)
        }.exceptionOrNull()

        assertTrue(failure is CancellationException)
        assertNull(state.lastErrorValue)
    }

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
        assertFalse(state.snapshotRequiresFullReconcile(RecordType.STEPS))
    }

    @Test
    fun expiredTokenSnapshotTargetIsCapturedAfterReplacementTokenCreation() = runTest {
        val replacementTarget = now.plusSeconds(5 * 60)
        val mutableClock = MutableClock(now)
        val source = FakeHealthSource(
            earliest = null,
            expireOldToken = true,
            onCreateToken = { mutableClock.current = replacementTarget },
        )
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "old-token")
            markSnapshotComplete(RecordType.STEPS)
        }
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = mutableClock,
            generationIds = GenerationIds { "replacement-generation" },
            historyFloor = floor,
        )

        coordinator.syncAll(maxPagesPerType = 1)

        assertEquals("fresh-token", state.changesToken(RecordType.STEPS))
        assertEquals(replacementTarget, source.earliestUntils.single())
        assertEquals(replacementTarget, uploader.batches.single().rangeEnd)
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
    fun initialBackfillMarksProviderConfirmedEmptyTypeCompleteWithoutUpload() = runTest {
        val source = FakeHealthSource(earliest = null)
        val state = FakeState(origin)
        val uploader = FakeUploader()

        coordinator(source, uploader, state).syncAll(maxPagesPerType = 1)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertTrue(uploader.batches.isEmpty())
        assertNull(state.dataAsOf())
        assertEquals(listOf("token", "earliest"), source.events)
    }

    @Test
    fun initialBackfillSkipsGuaranteedEmptyPrefixAndStartsAtEarliestRecord() = runTest {
        val longFloor = Instant.parse("2026-01-01T00:00:00Z")
        val earliest = Instant.parse("2026-04-20T08:00:00Z")
        val longNow = Instant.parse("2026-05-15T00:00:00Z")
        val source = FakeHealthSource(
            earliest = earliest,
            snapshotPages = mutableMapOf(
                null to SnapshotPage(listOf(recordAt("recent", earliest, longNow)), null),
            ),
        )
        val state = FakeState(origin)
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = Clock.fixed(longNow, ZoneOffset.UTC),
            generationIds = GenerationIds { "initial-generation" },
            historyFloor = longFloor,
        )

        coordinator.syncAll(maxPagesPerType = 1)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals(1, uploader.batches.size)
        assertEquals(earliest, uploader.batches.single().rangeStart)
        assertEquals(longNow, uploader.batches.single().rangeEnd)
        assertEquals(listOf("token", "earliest", "snapshot:first"), source.events)
    }

    @Test
    fun initialSnapshotTargetIsCapturedAfterChangesTokenCreation() = runTest {
        val observationTarget = now.plusSeconds(5 * 60)
        val mutableClock = MutableClock(now)
        val source = FakeHealthSource(
            earliest = floor,
            snapshotPages = mutableMapOf(
                null to SnapshotPage(listOf(record("observed")), null),
            ),
            onCreateToken = { mutableClock.current = observationTarget },
        )
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = FakeState(origin),
            clock = mutableClock,
            generationIds = GenerationIds { "observed-generation" },
            historyFloor = floor,
        )

        coordinator.syncAll(maxPagesPerType = 1)

        assertEquals(observationTarget, source.earliestUntils.single())
        assertEquals(observationTarget, source.snapshotUntils.single())
        assertEquals(observationTarget, uploader.batches.single().rangeEnd)
        assertTrue(source.events.indexOf("token") < source.events.indexOf("earliest"))
    }

    @Test
    fun pendingSnapshotRetainsItsPersistedTargetWhenSyncResumesLater() = runTest {
        val persistedTarget = now
        val mutableClock = MutableClock(now.plusSeconds(24 * 60 * 60))
        val source = FakeHealthSource(
            earliest = floor,
            snapshotPages = mutableMapOf(
                null to SnapshotPage(listOf(record("resumed")), null),
            ),
        )
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "initial-token")
            beginSnapshot(RecordType.STEPS, persistedTarget, requiresFullReconcile = false)
        }
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = mutableClock,
            generationIds = GenerationIds { "resumed-generation" },
            historyFloor = floor,
        )

        coordinator.syncAll(maxPagesPerType = 1)

        assertEquals(listOf("earliest", "snapshot:first"), source.events)
        assertEquals(persistedTarget, source.earliestUntils.single())
        assertEquals(persistedTarget, source.snapshotUntils.single())
        assertEquals(persistedTarget, uploader.batches.single().rangeEnd)
    }

    @Test
    fun cursorSnapshotRetainsItsTargetWhenNextPageRunsLater() = runTest {
        val mutableClock = MutableClock(now)
        val source = FakeHealthSource(
            earliest = floor,
            snapshotPages = mutableMapOf(
                null to SnapshotPage(listOf(record("page-one")), "page-2"),
                "page-2" to SnapshotPage(listOf(record("page-two")), null),
            ),
        )
        val state = FakeState(origin)
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = mutableClock,
            generationIds = GenerationIds { "resumed-cursor-generation" },
            historyFloor = floor,
        )

        coordinator.syncAll(maxPagesPerType = 1)
        mutableClock.current = now.plusSeconds(24 * 60 * 60)
        coordinator.syncAll(maxPagesPerType = 1)

        assertEquals(listOf(now, now), source.snapshotUntils)
        assertEquals(1, source.events.count { it == "token" })
        assertEquals(2, uploader.batches.size)
        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
    }

    @Test
    fun versionTwoInitialCursorIsMigratedAndSkipsItsOldEmptyWindows() = runTest {
        val earliest = floor.plusSeconds(12 * 60 * 60)
        val source = FakeHealthSource(
            earliest = earliest,
            snapshotPages = mutableMapOf(
                null to SnapshotPage(listOf(recordAt("migrated", earliest, now)), null),
            ),
        )
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "initial-token")
            setSnapshotCursor(
                RecordType.STEPS,
                SnapshotCursor(
                    generation = "v2-generation",
                    target = now,
                    rangeStart = floor,
                    rangeEnd = floor.plusSeconds(60 * 60),
                    knownEmpty = true,
                    emptyUntil = now,
                    formatVersion = 2,
                ),
            )
        }
        val uploader = FakeUploader()

        coordinator(source, uploader, state).syncAll(maxPagesPerType = 1)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals(earliest, uploader.batches.single().rangeStart)
        assertEquals(listOf("earliest", "snapshot:first"), source.events)
    }

    @Test
    fun versionTwoCursorUsesFullReconcileWhenAnotherTypeCompletedAndPreservesWatermark() = runTest {
        val freshWatermark = now.plusSeconds(60 * 60)
        val source = FakeHealthSource(
            earliest = null,
            enabled = listOf(RecordType.STEPS, RecordType.DISTANCE),
        )
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "initial-token")
            setChangesToken(RecordType.DISTANCE, "distance-token")
            markSnapshotComplete(RecordType.DISTANCE)
            setDataAsOf(freshWatermark)
            setSnapshotCursor(
                RecordType.STEPS,
                SnapshotCursor(
                    generation = "v2-empty-generation",
                    target = now,
                    rangeStart = floor,
                    rangeEnd = floor.plusSeconds(60 * 60),
                    knownEmpty = true,
                    emptyUntil = now,
                    formatVersion = 2,
                ),
            )
        }
        val uploader = FakeUploader()

        coordinator(source, uploader, state).syncAll(maxPagesPerType = 1)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertTrue(state.isSnapshotComplete(RecordType.DISTANCE))
        assertEquals(1, uploader.batches.size)
        assertTrue(uploader.batches.single().records.isEmpty())
        assertEquals(floor, uploader.batches.single().rangeStart)
        assertEquals(now, uploader.batches.single().rangeEnd)
        assertEquals(freshWatermark, state.dataAsOf())
    }

    @Test
    fun allLegacyCursorsAreMigratedBeforeAnEarlierTypeCanCompleteAndFail() = runTest {
        val earliest = floor.plusSeconds(12 * 60 * 60)
        val source = FakeHealthSource(
            earliest = earliest,
            enabled = listOf(RecordType.STEPS, RecordType.DISTANCE),
            failChangesOnceFor = mutableSetOf(RecordType.STEPS),
        )
        val state = FakeState(origin).apply {
            listOf(RecordType.STEPS, RecordType.DISTANCE).forEach { type ->
                setChangesToken(type, "${type.wireName}-token")
                setSnapshotCursor(
                    type,
                    SnapshotCursor(
                        generation = "v2-${type.wireName}",
                        target = now,
                        rangeStart = floor,
                        rangeEnd = floor.plusSeconds(60 * 60),
                        knownEmpty = true,
                        emptyUntil = now,
                        formatVersion = 2,
                    ),
                )
            }
        }
        val uploader = FakeUploader()
        val coordinator = coordinator(source, uploader, state)

        runCatching { coordinator.syncAll(maxPagesPerType = 2) }
            .onSuccess { error("Expected first type changes failure") }

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals(now, state.snapshotTarget(RecordType.DISTANCE))
        assertFalse(state.snapshotRequiresFullReconcile(RecordType.DISTANCE))

        coordinator.syncAll(maxPagesPerType = 1)

        val distanceBatch = uploader.batches.single { it.recordType == RecordType.DISTANCE }
        assertEquals(earliest, distanceBatch.rangeStart)
        assertEquals(now, distanceBatch.rangeEnd)
        assertTrue(state.isSnapshotComplete(RecordType.DISTANCE))
    }

    @Test
    fun fullReconcileOfEmptyHistoryUsesOneProviderConfirmedEmptySnapshot() = runTest {
        val longFloor = Instant.parse("2026-01-01T00:00:00Z")
        val longNow = Instant.parse("2026-05-15T00:00:00Z")
        val mutableClock = MutableClock(longNow)
        val source = FakeHealthSource(earliest = null)
        val state = FakeState(origin).apply {
            beginSnapshot(RecordType.STEPS, longNow, requiresFullReconcile = true)
        }
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = mutableClock,
            generationIds = GenerationIds { "empty-generation" },
            historyFloor = longFloor,
        )

        coordinator.syncAll(maxPagesPerType = 1)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals(1, uploader.batches.size)
        assertEquals(longFloor, uploader.batches.single().rangeStart)
        assertEquals(longNow, uploader.batches.single().rangeEnd)
        assertTrue(uploader.batches.single().records.isEmpty())
        assertTrue(uploader.batches.single().finalPage == true)
        assertTrue(source.events.none { it.startsWith("snapshot:") })
    }

    @Test
    fun persistedMonthlyEmptyCursorFastForwardsWithoutResettingPairingState() = runTest {
        val longFloor = Instant.parse("2000-01-01T00:00:00Z")
        val firstMonthEnd = longFloor.plus(Duration.ofDays(30))
        val longNow = Instant.parse("2026-08-21T08:00:00Z")
        val source = FakeHealthSource(earliest = null)
        val state = FakeState(origin).apply {
            setChangesToken(RecordType.STEPS, "existing-token")
            setSnapshotCursor(
                RecordType.STEPS,
                SnapshotCursor(
                    generation = "persisted-v3-generation",
                    target = longNow,
                    rangeStart = longFloor,
                    rangeEnd = firstMonthEnd,
                    knownEmpty = true,
                    emptyUntil = longNow,
                ),
            )
        }
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = Clock.fixed(longNow, ZoneOffset.UTC),
            generationIds = GenerationIds { "unused-generation" },
            historyFloor = longFloor,
        )

        coordinator.syncAll(maxPagesPerType = 2)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals("existing-token", state.changesToken(RecordType.STEPS))
        assertEquals(2, uploader.batches.size)
        assertEquals(firstMonthEnd, uploader.batches[1].rangeStart)
        assertEquals(longNow, uploader.batches[1].rangeEnd)
        assertTrue(source.events.none { it == "token" || it == "earliest" })
    }

    @Test
    fun fullReconcileSkipsMultiYearGapAfterAnOldRecordWindow() = runTest {
        val longFloor = Instant.parse("2000-01-01T00:00:00Z")
        val firstRecord = Instant.parse("2001-01-01T00:00:00Z")
        val longNow = Instant.parse("2026-08-21T08:00:00Z")
        val source = FakeHealthSource(
            earliestAnswers = mutableListOf(firstRecord, null),
            snapshotPages = mutableMapOf(
                null to SnapshotPage(listOf(recordAt("old", firstRecord, firstRecord)), null),
            ),
        )
        val state = FakeState(origin).apply {
            beginSnapshot(RecordType.STEPS, longNow, requiresFullReconcile = true)
        }
        val uploader = FakeUploader()
        val coordinator = SyncCoordinator(
            source = source,
            uploader = uploader,
            state = state,
            clock = Clock.fixed(longNow, ZoneOffset.UTC),
            generationIds = GenerationIds { "gap-generation" },
            historyFloor = longFloor,
        )

        coordinator.syncAll(maxPagesPerType = 3)

        assertTrue(state.isSnapshotComplete(RecordType.STEPS))
        assertEquals(3, uploader.batches.size)
        assertEquals(longFloor, uploader.batches[0].rangeStart)
        assertEquals(firstRecord, uploader.batches[0].rangeEnd)
        assertTrue(uploader.batches[0].records.isEmpty())
        assertEquals(firstRecord, uploader.batches[1].rangeStart)
        assertEquals(firstRecord.plus(Duration.ofDays(30)), uploader.batches[1].rangeEnd)
        assertEquals(listOf("old"), uploader.batches[1].records.map(ExportRecord::recordId))
        assertEquals(firstRecord.plus(Duration.ofDays(30)), uploader.batches[2].rangeStart)
        assertEquals(longNow, uploader.batches[2].rangeEnd)
        assertTrue(uploader.batches[2].records.isEmpty())
        assertEquals(2, source.events.count { it == "earliest" })
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

    private fun recordAt(id: String, start: Instant, modified: Instant) = ExportRecord(
        recordId = id,
        type = RecordType.STEPS,
        startTime = start,
        endTime = start.plusSeconds(60),
        dataOrigin = origin,
        lastModifiedTime = modified,
        values = buildJsonObject { put("count", 100) },
    )

    private class FakeHealthSource(
        private val earliest: Instant? = null,
        private val earliestAnswers: MutableList<Instant?>? = null,
        private val snapshotPages: MutableMap<String?, SnapshotPage> = mutableMapOf(),
        private val expireOldToken: Boolean = false,
        private val changes: List<ExportChange> = emptyList(),
        private val enabled: List<RecordType> = listOf(RecordType.STEPS),
        private val onCreateToken: () -> Unit = {},
        private val failChangesOnceFor: MutableSet<RecordType> = mutableSetOf(),
    ) : HealthDataSource {
        val events = mutableListOf<String>()
        val earliestUntils = mutableListOf<Instant>()
        val snapshotUntils = mutableListOf<Instant>()

        override suspend fun enabledTypes() = enabled

        override suspend fun findEarliest(
            type: RecordType,
            dataOrigin: String,
            from: Instant,
            until: Instant,
        ): Instant? {
            events += "earliest"
            earliestUntils += until
            return if (earliestAnswers?.isNotEmpty() == true) {
                earliestAnswers.removeAt(0)
            } else {
                earliest
            }
        }

        override suspend fun readSnapshotPage(
            type: RecordType,
            dataOrigin: String,
            from: Instant,
            until: Instant,
            pageToken: String?,
        ): SnapshotPage {
            events += "snapshot:${pageToken ?: "first"}"
            snapshotUntils += until
            return snapshotPages[pageToken] ?: SnapshotPage(emptyList(), null)
        }

        override suspend fun createChangesToken(type: RecordType, dataOrigin: String): String {
            events += "token"
            onCreateToken()
            return if (events.any { it == "changes:old-token" }) "fresh-token" else "initial-token"
        }

        override suspend fun readChanges(
            type: RecordType,
            dataOrigin: String,
            token: String,
        ): ChangePage {
            events += "changes:$token"
            if (failChangesOnceFor.remove(type)) throw IOException("changes unavailable")
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
        private val reconcile = mutableSetOf<RecordType>()
        private val targets = mutableMapOf<RecordType, Instant>()
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
            targets -= type
        }
        override fun isSnapshotComplete(type: RecordType) = type in complete
        override fun markSnapshotComplete(type: RecordType) {
            complete += type
            cursors -= type
            reconcile -= type
            targets -= type
        }
        override fun snapshotRequiresFullReconcile(type: RecordType) = type in reconcile
        override fun snapshotTarget(type: RecordType) = targets[type]
        override fun beginSnapshot(
            type: RecordType,
            target: Instant,
            requiresFullReconcile: Boolean,
        ) {
            complete -= type
            cursors -= type
            targets[type] = target
            if (requiresFullReconcile) reconcile += type else reconcile -= type
        }
        override fun beginSnapshotWithChangesToken(
            type: RecordType,
            newToken: String,
            target: Instant,
            requiresFullReconcile: Boolean,
        ) {
            tokens[type] = newToken
            beginSnapshot(type, target, requiresFullReconcile)
        }
        override fun setDataAsOf(value: Instant) {
            if (asOf == null || value > asOf) asOf = value
        }
        override fun dataAsOf() = asOf
        override fun setLastSync(value: Instant) { lastSyncValue = value }
        override fun setLastError(value: String?) { lastErrorValue = value }
    }

    private class MutableClock(
        var current: Instant,
        private val currentZone: ZoneId = ZoneOffset.UTC,
    ) : Clock() {
        override fun getZone(): ZoneId = currentZone

        override fun withZone(zone: ZoneId): Clock = MutableClock(current, zone)

        override fun instant(): Instant = current
    }
}
