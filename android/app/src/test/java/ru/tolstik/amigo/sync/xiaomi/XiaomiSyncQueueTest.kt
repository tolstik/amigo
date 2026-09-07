package ru.tolstik.amigo.sync.xiaomi

import java.time.Duration
import java.time.Instant
import org.junit.Assert.*
import org.junit.Test

class XiaomiSyncQueueTest {
    private val now = Instant.parse("2026-09-07T08:00:00Z")
    private val floor = Instant.parse("2000-01-01T00:00:00Z")
    private val state = mutableMapOf<String, Any>()
    private val storage = memoryXiaomiPreferences(state)
    private val preferences = XiaomiSyncPreferences(storage)
    private val queue = XiaomiSyncQueue(preferences, floor)

    @Test
    fun upgradeKeepsPartialMonthlyAndHistoricalPagesWhileFreshStepsFinishInOneRun() {
        preferences.enable("synthetic-account", "ru")
        preferences.markRegionDiscovered("synthetic-account", "ru")
        val monthly = XiaomiCursor(
            "monthly-steps", now.minusSeconds(31 * 86400L), now.minusSeconds(86400),
            "monthly-next", 20, now.minusSeconds(86401), setOf("a".repeat(64)),
        )
        val historical = monthly.copy(
            snapshotId = "historical-steps", rangeStart = now.minusSeconds(90 * 86400L),
            rangeEnd = now.minusSeconds(60 * 86400L), nextKey = "historical-next", pageIndex = 24,
        )
        preferences.completeHistoryWindow(XiaomiMetric.STEPS, historical.rangeEnd, null)
        preferences.setHistoryCursor(XiaomiMetric.STEPS, historical)
        preferences.setRefreshCursor(XiaomiMetric.STEPS, monthly)
        preferences.setRefreshRound(XiaomiRefreshRound(monthly.rangeEnd, 30))
        val oldState = state.toMap()

        queue.prepare(now, 3, XiaomiSyncMode.ROUTINE)
        assertEquals(monthly, preferences.refreshCursor(XiaomiMetric.STEPS))
        assertEquals(historical, preferences.historyCursor(XiaomiMetric.STEPS))
        oldState.forEach { (key, value) -> assertEquals(key, value, state[key]) }
        val calls = mutableListOf<XiaomiPageWork>()
        val budget = XiaomiPageBudget(40) { 0L }
        while (budget.take()) {
            val work = queue.next(emptySet()) ?: break
            calls += work
            if (work.lane == XiaomiCursorLane.RECENT) {
                val cursor = preferences.refreshCursor(work.metric, work.lane)!!
                if (work.metric == XiaomiMetric.STEPS && cursor.pageIndex < 2) {
                    preferences.setRefreshCursor(work.metric, cursor.copy(pageIndex = cursor.pageIndex + 1), work.lane)
                } else {
                    complete(work.metric, work.lane)
                }
            }
        }
        queue.finishRounds()
        assertEquals(3, calls.count { it == XiaomiPageWork(XiaomiMetric.STEPS, XiaomiCursorLane.RECENT) })
        assertEquals(now, preferences.status(true).stepsAsOf)
        assertNull(preferences.refreshCursor(XiaomiMetric.STEPS, XiaomiCursorLane.RECENT))
        assertEquals(monthly, preferences.refreshCursor(XiaomiMetric.STEPS))
        assertEquals(historical, preferences.historyCursor(XiaomiMetric.STEPS))
        assertTrue(queue.pending())
    }

    @Test
    fun weeklyAndManualRequestsNeverMoveAnUnfinishedRecentTargetAfterRestart() {
        queue.prepare(now, 3, XiaomiSyncMode.ROUTINE)
        val first = preferences.refreshCursor(XiaomiMetric.STEPS, XiaomiCursorLane.RECENT)!!
        preferences.setRefreshCursor(XiaomiMetric.STEPS, first.copy(nextKey = "next", pageIndex = 1), XiaomiCursorLane.RECENT)
        complete(XiaomiMetric.SLEEP, XiaomiCursorLane.RECENT)
        val recentState = state.filterKeys { "recent_" in it }

        XiaomiSyncQueue(XiaomiSyncPreferences(storage), floor).prepare(now.plusSeconds(7200), 30, XiaomiSyncMode.FORCE_REFRESH)
        assertEquals(recentState, state.filterKeys { "recent_" in it })
        assertEquals(XiaomiRefreshRound(now, 3), preferences.refreshRound(XiaomiCursorLane.RECENT))
        val weekly = preferences.refreshRound()!!
        assertEquals(30L, weekly.days)
        XiaomiMetric.entries.forEach { metric ->
            val cursor = preferences.refreshCursor(metric)!!
            assertEquals(weekly.target, cursor.rangeEnd)
            assertEquals(weekly.target.minusSeconds(30 * 86400L), cursor.rangeStart)
        }
        queue.prepare(now.plusSeconds(10800), 3, XiaomiSyncMode.FORCE_REFRESH)
        assertEquals(weekly, preferences.refreshRound())
        assertEquals(recentState, state.filterKeys { "recent_" in it })
    }

    @Test
    fun delayedContinuationStartsFreshThreeDayRoundWithoutWaitingForMonthlyHistory() {
        queue.prepare(now, 30, XiaomiSyncMode.ROUTINE)
        XiaomiMetric.entries.forEach { complete(it, XiaomiCursorLane.RECENT) }
        queue.finishRounds()
        val monthly = preferences.refreshCursor(XiaomiMetric.STEPS)!!

        queue.prepare(now.plusSeconds(3599), 3, XiaomiSyncMode.BACKFILL_CONTINUATION)
        assertNull(preferences.refreshRound(XiaomiCursorLane.RECENT))
        val wake = now.plusSeconds(5 * 3600)
        queue.prepare(wake, 3, XiaomiSyncMode.BACKFILL_CONTINUATION)
        assertEquals(XiaomiRefreshRound(wake, 3), preferences.refreshRound(XiaomiCursorLane.RECENT))
        assertEquals(monthly, preferences.refreshCursor(XiaomiMetric.STEPS))
    }

    @Test
    fun legacySingleCursorRemainsInItsOriginalLaneWhenRecentCompletesFirst() {
        val old = XiaomiCursor("legacy", now.minusSeconds(33 * 86400L), now.minusSeconds(3 * 86400L), "next", 10)
        preferences.setHistoryCursor(XiaomiMetric.STEPS, old) // Pre-1.4.1 key, no history watermark.
        queue.prepare(now, 3, XiaomiSyncMode.ROUTINE)
        complete(XiaomiMetric.STEPS, XiaomiCursorLane.RECENT)
        assertEquals(old, preferences.refreshCursor(XiaomiMetric.STEPS))
        assertNull(preferences.historyCursor(XiaomiMetric.STEPS))
        assertEquals(now.minusSeconds(3 * 86400L), preferences.historyEnd(XiaomiMetric.STEPS))
    }

    @Test
    fun persistedFairnessOffersAllTenMetricsAndBothBackgroundLanes() {
        queue.prepare(now, 30, XiaomiSyncMode.ROUTINE)
        XiaomiMetric.entries.forEach { preferences.completeHistoryWindow(it, now.minusSeconds(90 * 86400L), null) }
        val calls = (1..40).map {
            // Short runs / process restarts retain lane shares and per-lane metric order.
            XiaomiSyncQueue(XiaomiSyncPreferences(storage), floor).next(emptySet())!!
        }
        assertEquals(32, calls.count { it.lane == XiaomiCursorLane.RECENT })
        assertEquals(4, calls.count { it.lane == XiaomiCursorLane.REFRESH })
        assertEquals(4, calls.count { it.lane == XiaomiCursorLane.HISTORY })
        assertEquals(XiaomiMetric.entries.toSet(), calls.filter { it.lane == XiaomiCursorLane.RECENT }.map { it.metric }.toSet())
    }

    @Test
    fun failedMetricIsSkippedWithinRunAndResumedWithTheSameCursorNextRun() {
        queue.prepare(now, 3, XiaomiSyncMode.ROUTINE)
        val failed = queue.next(emptySet())!!
        val cursor = preferences.refreshCursor(failed.metric, failed.lane)
        val others = (1..40).map { queue.next(setOf(failed)) }
        assertFalse(others.contains(failed))
        assertTrue(others.any { it?.metric == XiaomiMetric.SLEEP })
        assertEquals(cursor, preferences.refreshCursor(failed.metric, failed.lane))
        assertTrue((1..10).map { queue.next(emptySet()) }.contains(failed))
    }

    @Test
    fun eachAttemptConsumesBudgetAndDeadlineStopsBeforeAnotherPage() {
        var nanos = 0L
        val budget = XiaomiPageBudget(40) { nanos }
        assertTrue(budget.take())
        nanos = Duration.ofSeconds(89).toNanos()
        assertTrue(budget.take())
        nanos = Duration.ofSeconds(90).toNanos()
        assertFalse(budget.take())
        val limited = XiaomiPageBudget(3) { 0L }
        repeat(3) { assertTrue(limited.take()) }
        assertFalse(limited.take())
    }

    private fun complete(metric: XiaomiMetric, lane: XiaomiCursorLane) {
        val cursor = preferences.refreshCursor(metric, lane)!!
        preferences.completeRefreshWindow(metric, cursor.rangeStart, cursor.rangeEnd, cursor.rangeEnd, lane)
    }
}
