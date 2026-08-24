package ru.tolstik.amigo.sync.xiaomi

import java.io.IOException
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class XiaomiSyncCoordinatorTest {
    @Test
    fun safeServerRejectionRemainsVisibleInsteadOfMasqueradingAsCloudFailure() {
        assertEquals(
            "mi_fitness_not_enabled",
            xiaomiSyncErrorCode(
                IOException("Amigo server returned HTTP 409 (mi_fitness_not_enabled)"),
            ),
        )
    }

    @Test
    fun unsafeOrProviderErrorsRemainGeneric() {
        assertEquals(
            "invalid_cloud_response",
            xiaomiSyncErrorCode(IOException("provider body: private-value")),
        )
        assertEquals(
            "network_error",
            xiaomiSyncErrorCode(XiaomiCloudException.Network(IOException("offline"))),
        )
    }

    @Test
    fun onlySnapshotSequenceConflictsTriggerTargetedRestart() {
        listOf(
            "batch_id_conflict",
            "snapshot_already_finalised",
            "snapshot_metadata_conflict",
            "snapshot_page_out_of_order",
            "snapshot_pages_incomplete",
            "snapshot_record_repeated",
        ).forEach { code ->
            assertTrue(
                code,
                shouldRestartXiaomiSnapshot(IOException("Amigo server returned HTTP 409 ($code)")),
            )
        }
        assertFalse(
            shouldRestartXiaomiSnapshot(
                IOException("Amigo server returned HTTP 409 (mi_fitness_not_enabled)"),
            ),
        )
    }

    @Test
    fun restartPreservesOnlyTheAffectedRangeAndClearsPartialPageState() {
        val start = Instant.parse("2026-08-01T00:00:00Z")
        val end = Instant.parse("2026-08-24T00:00:00Z")
        val cursor = XiaomiCursor(
            snapshotId = "old-snapshot",
            rangeStart = start,
            rangeEnd = end,
            nextKey = "private-provider-cursor",
            pageIndex = 3,
            sourceDataAsOf = end.minusSeconds(1),
            seenRecordHashes = setOf(xiaomiRecordHash("record-1")),
        )

        assertEquals(
            XiaomiCursor("new-snapshot", start, end),
            restartXiaomiCursor(cursor, "new-snapshot"),
        )
    }
}
