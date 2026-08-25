package ru.tolstik.amigo.sync.xiaomi

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class XiaomiRefreshLaneTest {
    private val floor = Instant.parse("2000-01-01T00:00:00Z")
    private val historicalEnd = Instant.parse("2026-07-22T09:00:00Z")
    private val recentEnd = Instant.parse("2026-08-25T09:00:00Z")

    @Test
    fun legacyInitialCursorMigratesToRefreshLane() {
        val cursor = cursor(
            start = recentEnd.minusSeconds(3 * 86_400L),
            end = recentEnd,
        )

        assertEquals(
            XiaomiCursorLane.REFRESH,
            classifyLegacyXiaomiCursor(historyEnd = null, cursor = cursor),
        )
    }

    @Test
    fun legacyHistoricalCursorKeepsItsExactBackfillLane() {
        val cursor = cursor(
            start = historicalEnd.minusSeconds(30 * 86_400L),
            end = historicalEnd,
        )

        assertEquals(
            XiaomiCursorLane.HISTORY,
            classifyLegacyXiaomiCursor(historicalEnd, cursor),
        )
    }

    @Test
    fun legacyPostBackfillCursorMigratesToRefreshLane() {
        val cursor = cursor(
            start = recentEnd.minusSeconds(3 * 86_400L),
            end = recentEnd,
        )

        assertEquals(
            XiaomiCursorLane.REFRESH,
            classifyLegacyXiaomiCursor(floor, cursor),
        )
    }

    @Test
    fun recentPageTakesPriorityWithoutDestroyingTheHistoricalPage() {
        assertEquals(
            XiaomiCursorLane.REFRESH,
            selectXiaomiCursorLane(
                hasRefreshCursor = true,
                hasHistoryCursor = true,
                historyEnd = historicalEnd,
                historyFloor = floor,
            ),
        )
        assertEquals(
            XiaomiCursorLane.HISTORY,
            selectXiaomiCursorLane(
                hasRefreshCursor = false,
                hasHistoryCursor = true,
                historyEnd = historicalEnd,
                historyFloor = floor,
            ),
        )
    }

    @Test
    fun historicalWatermarkCreatesBackfillOnlyUntilTheFloor() {
        assertEquals(
            XiaomiCursorLane.HISTORY,
            selectXiaomiCursorLane(
                hasRefreshCursor = false,
                hasHistoryCursor = false,
                historyEnd = historicalEnd,
                historyFloor = floor,
            ),
        )
        assertEquals(
            null,
            selectXiaomiCursorLane(
                hasRefreshCursor = false,
                hasHistoryCursor = false,
                historyEnd = floor,
                historyFloor = floor,
            ),
        )
    }

    @Test
    fun completedRangeMustCoverTheExactPersistedRefreshRound() {
        val round = XiaomiRefreshRound(target = recentEnd, days = 3)

        assertTrue(
            xiaomiRefreshCovers(
                rangeStart = recentEnd.minusSeconds(3 * 86_400L),
                rangeEnd = recentEnd,
                round = round,
            ),
        )
        assertFalse(
            xiaomiRefreshCovers(
                rangeStart = recentEnd.minusSeconds(3 * 86_400L).plusSeconds(1),
                rangeEnd = recentEnd,
                round = round,
            ),
        )
        assertFalse(
            xiaomiRefreshCovers(
                rangeStart = recentEnd.minusSeconds(3 * 86_400L),
                rangeEnd = recentEnd.minusSeconds(1),
                round = round,
            ),
        )
    }

    @Test
    fun weeklyRoundIsNotSatisfiedByACompletedThreeDayRefresh() {
        assertFalse(
            xiaomiRefreshCovers(
                rangeStart = recentEnd.minusSeconds(3 * 86_400L),
                rangeEnd = recentEnd,
                round = XiaomiRefreshRound(target = recentEnd, days = 30),
            ),
        )
    }

    @Test
    fun firstRefreshSeedsHistoryWithoutMovingAnExistingBackfillWatermark() {
        val initialStart = recentEnd.minusSeconds(3 * 86_400L)

        assertEquals(
            initialStart,
            xiaomiHistoryEndAfterRefresh(current = null, initialStart = initialStart),
        )
        assertEquals(
            historicalEnd,
            xiaomiHistoryEndAfterRefresh(
                current = historicalEnd,
                initialStart = initialStart,
            ),
        )
    }

    @Test
    fun initialRecentCoverageIsRequiredEvenDuringBackfillContinuation() {
        assertTrue(
            shouldStartXiaomiRefresh(
                lastRangeStart = null,
                lastRangeEnd = null,
                target = recentEnd,
                refreshDays = 3,
                mode = XiaomiSyncMode.BACKFILL_CONTINUATION,
            ),
        )
    }

    @Test
    fun continuationDoesNotRestartCompletedRecentCoverage() {
        assertFalse(
            shouldStartXiaomiRefresh(
                lastRangeStart = recentEnd.minusSeconds(3 * 86_400L),
                lastRangeEnd = recentEnd,
                target = recentEnd.plusSeconds(7 * 86_400L),
                refreshDays = 30,
                mode = XiaomiSyncMode.BACKFILL_CONTINUATION,
            ),
        )
    }

    @Test
    fun routineRefreshStartsAtTheHourlyBoundaryButNotBeforeIt() {
        val previousStart = recentEnd.minusSeconds(3 * 86_400L)

        assertFalse(
            shouldStartXiaomiRefresh(
                previousStart,
                recentEnd,
                recentEnd.plusSeconds(3_599),
                refreshDays = 3,
                mode = XiaomiSyncMode.ROUTINE,
            ),
        )
        assertTrue(
            shouldStartXiaomiRefresh(
                previousStart,
                recentEnd,
                recentEnd.plusSeconds(3_600),
                refreshDays = 3,
                mode = XiaomiSyncMode.ROUTINE,
            ),
        )
    }

    @Test
    fun weeklyReconciliationWidensAFreshThreeDayWindow() {
        assertTrue(
            shouldStartXiaomiRefresh(
                lastRangeStart = recentEnd.minusSeconds(3 * 86_400L),
                lastRangeEnd = recentEnd,
                target = recentEnd.plusSeconds(60),
                refreshDays = 30,
                mode = XiaomiSyncMode.ROUTINE,
            ),
        )
    }

    @Test
    fun manualSyncRequestsANewWindowWithoutReplacingTheSameTarget() {
        val previousStart = recentEnd.minusSeconds(3 * 86_400L)

        assertFalse(
            shouldStartXiaomiRefresh(
                previousStart,
                recentEnd,
                recentEnd,
                refreshDays = 3,
                mode = XiaomiSyncMode.FORCE_REFRESH,
            ),
        )
        assertTrue(
            shouldStartXiaomiRefresh(
                previousStart,
                recentEnd,
                recentEnd.plusSeconds(1),
                refreshDays = 3,
                mode = XiaomiSyncMode.FORCE_REFRESH,
            ),
        )
    }

    private fun cursor(start: Instant, end: Instant) = XiaomiCursor(
        snapshotId = "legacy-snapshot",
        rangeStart = start,
        rangeEnd = end,
        nextKey = "opaque-page",
        pageIndex = 16,
    )
}
