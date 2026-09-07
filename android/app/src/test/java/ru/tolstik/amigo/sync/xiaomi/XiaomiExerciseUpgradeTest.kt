package ru.tolstik.amigo.sync.xiaomi

import java.time.Instant
import org.junit.Assert.*
import org.junit.Test

class XiaomiExerciseUpgradeTest {
    @Test
    fun upgradeIsAtomicIdempotentAndPreservesOtherMetricsAndRefreshBounds() {
        val state = mutableMapOf<String, Any>(
            "enabled" to true, "account_fingerprint" to "synthetic", "region" to "ru",
            "refresh_round_target" to "2026-09-01T09:00:00Z", "refresh_round_days" to 3L,
        )
        val storage = memoryXiaomiPreferences(state)
        val preferences = XiaomiSyncPreferences(storage)
        val end = Instant.parse("2026-09-01T09:00:00Z")
        val old = XiaomiCursor("old-snapshot", end.minusSeconds(3 * 86400), end, "page-two", 1, seenRecordHashes = setOf("a".repeat(64)))
        XiaomiMetric.entries.forEach { metric ->
            state["metric.${metric.type.wireName}.history_end"] = "2000-01-01T00:00:00Z"
            preferences.setRefreshCursor(metric, old)
        }
        val otherState = state.filterKeys { !it.startsWith("metric.exercise.") }
        preferences.prepareExerciseDetailsUpgrade(end.plusSeconds(3600))
        assertEquals(otherState, state.filterKeys { !it.startsWith("metric.exercise.") && it != "exercise_details_version" })
        val refreshed = preferences.refreshCursor(XiaomiMetric.EXERCISE)!!
        assertEquals(old.rangeStart, refreshed.rangeStart)
        assertEquals(old.rangeEnd, refreshed.rangeEnd)
        assertNotEquals(old.snapshotId, refreshed.snapshotId)
        assertEquals(0, refreshed.pageIndex)
        assertNull(refreshed.nextKey)
        assertTrue(refreshed.seenRecordHashes.isEmpty())
        assertEquals(end.plusSeconds(3600), preferences.historyEnd(XiaomiMetric.EXERCISE))
        val migrated = state.toMap()
        // Simulate process restart: the persisted version prevents another rewind.
        XiaomiSyncPreferences(storage).prepareExerciseDetailsUpgrade(end.plusSeconds(7200))
        assertEquals(migrated, state)
    }

    @Test
    fun legacyUnfinishedExerciseHistoryIsRestartedWhileCompletedDataRemainsOnServer() {
        val state = mutableMapOf<String, Any>()
        val preferences = XiaomiSyncPreferences(memoryXiaomiPreferences(state))
        val end = Instant.parse("2026-08-01T09:00:00Z")
        state["metric.exercise.history_end"] = end.toString()
        preferences.setHistoryCursor(XiaomiMetric.EXERCISE, XiaomiCursor("old-history", end.minusSeconds(30 * 86400), end, "next", 2))
        val now = end.plusSeconds(36 * 86400)
        preferences.prepareExerciseDetailsUpgrade(now)
        assertNull(preferences.historyCursor(XiaomiMetric.EXERCISE))
        assertEquals(now, preferences.historyEnd(XiaomiMetric.EXERCISE))
    }

}
