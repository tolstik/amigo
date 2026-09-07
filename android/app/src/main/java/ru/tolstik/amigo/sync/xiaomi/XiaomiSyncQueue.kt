package ru.tolstik.amigo.sync.xiaomi

import java.time.Duration
import java.time.Instant
import java.util.UUID

/** Independent immutable three-day and weekly rounds; old cursors finish in place. */
internal class XiaomiSyncQueue(
    private val preferences: XiaomiSyncPreferences,
    private val historyFloor: Instant,
) {
    fun prepare(target: Instant, refreshDays: Long, mode: XiaomiSyncMode) {
        preferences.migrateLegacyRefreshCursors()
        prepareLane(XiaomiCursorLane.RECENT, target, 3, mode, requested = true)
        prepareLane(XiaomiCursorLane.REFRESH, target, 30, mode, requested = refreshDays > 3)
    }

    private fun prepareLane(
        lane: XiaomiCursorLane,
        target: Instant,
        days: Long,
        mode: XiaomiSyncMode,
        requested: Boolean,
    ) {
        val inherited = XiaomiMetric.entries.firstNotNullOfOrNull { preferences.refreshCursor(it, lane) }
        // Never replace a round while any metric is still uploading. This also adopts
        // pre-upgrade 3/30-day cursors with their exact page tokens, bounds and hashes.
        val round = preferences.refreshRound(lane) ?: inherited?.let {
            XiaomiRefreshRound(it.rangeEnd, Duration.between(it.rangeStart, it.rangeEnd).toDays().coerceIn(3, 30))
        } ?: run {
            val due = requested && XiaomiMetric.entries.any { metric ->
                shouldStartXiaomiRefresh(
                    preferences.refreshStart(metric, lane),
                    preferences.refreshEnd(metric, lane),
                    target,
                    days,
                    // A continuation may wake hours late. Check recent freshness on every
                    // wake, while keeping an unfinished round's target fixed.
                    if (mode == XiaomiSyncMode.BACKFILL_CONTINUATION) XiaomiSyncMode.ROUTINE else mode,
                )
            }
            if (!due) return
            XiaomiRefreshRound(target, days)
        }
        preferences.setRefreshRound(round, lane)
        XiaomiMetric.entries.forEach { metric ->
            if (preferences.refreshCursor(metric, lane) == null && !covered(metric, lane, round)) {
                preferences.setRefreshCursor(
                    metric,
                    XiaomiCursor(
                        "mi-${metric.type.wireName}-${UUID.randomUUID()}",
                        round.target.minus(Duration.ofDays(round.days)),
                        round.target,
                    ),
                    lane,
                )
            }
        }
    }

    private fun covered(metric: XiaomiMetric, lane: XiaomiCursorLane, round: XiaomiRefreshRound) =
        xiaomiRefreshCovers(preferences.refreshStart(metric, lane), preferences.refreshEnd(metric, lane), round)

    fun finishRounds() {
        listOf(XiaomiCursorLane.RECENT, XiaomiCursorLane.REFRESH).forEach { lane ->
            val round = preferences.refreshRound(lane) ?: return@forEach
            if (XiaomiMetric.entries.all { preferences.refreshCursor(it, lane) == null && covered(it, lane, round) }) {
                preferences.clearRefreshRound(round, lane)
            }
        }
    }

    fun pending(): Boolean = XiaomiCursorLane.entries.any { lane ->
        XiaomiMetric.entries.any { available(XiaomiPageWork(it, lane)) }
    }

    private fun available(work: XiaomiPageWork): Boolean = when (work.lane) {
        XiaomiCursorLane.RECENT, XiaomiCursorLane.REFRESH ->
            preferences.refreshCursor(work.metric, work.lane) != null
        XiaomiCursorLane.HISTORY -> preferences.historyCursor(work.metric) != null ||
            preferences.historyEnd(work.metric)?.let { it > historyFloor } == true
    }

    fun next(blocked: Set<XiaomiPageWork>): XiaomiPageWork? {
        // Persist weighted fairness across short runs: eight recent pages, one monthly
        // page and one history page. Unused shares immediately go to available work.
        val preferred = when (preferences.pageTurn()) {
            8 -> XiaomiCursorLane.REFRESH
            9 -> XiaomiCursorLane.HISTORY
            else -> XiaomiCursorLane.RECENT
        }
        val lanes = (listOf(preferred) + XiaomiCursorLane.entries).distinct()
        for (lane in lanes) {
            val start = preferences.nextMetricIndex(lane)
            for (offset in XiaomiMetric.entries.indices) {
                val index = (start + offset).mod(XiaomiMetric.entries.size)
                val work = XiaomiPageWork(XiaomiMetric.entries[index], lane)
                if (work !in blocked && available(work)) {
                    preferences.setNextMetricIndex(index + 1, lane)
                    preferences.advancePageTurn()
                    return work
                }
            }
        }
        return null
    }
}

internal data class XiaomiPageWork(val metric: XiaomiMetric, val lane: XiaomiCursorLane)

/** No new provider page after 90 seconds; an in-flight signed upload keeps its timeout. */
internal class XiaomiPageBudget(
    private val maxPages: Int,
    private val nanoTime: () -> Long = System::nanoTime,
) {
    private val started = nanoTime()
    private var attempted = 0

    fun available(): Boolean =
        attempted < maxPages && nanoTime() - started < Duration.ofSeconds(90).toNanos()

    fun take(): Boolean {
        if (!available()) return false
        attempted++
        return true
    }
}

internal class XiaomiPageBudgetExhausted : Exception()
