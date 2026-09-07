package ru.tolstik.amigo.sync.xiaomi

import android.content.Context
import java.time.Instant
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import ru.tolstik.amigo.sync.wire.CanonicalJson

data class XiaomiLocalStatus(
    val enabled: Boolean,
    val hasCredentials: Boolean,
    val status: String,
    val active: Boolean,
    val region: String?,
    val lastSync: Instant?,
    val dataAsOf: Instant?,
    val lastErrorCode: String?,
    val completedTypes: Int,
    val stepsAsOf: Instant?,
    val stepsPending: Boolean,
)

internal class XiaomiSyncPreferences(private val values: android.content.SharedPreferences) {
    constructor(context: Context) : this(context.getSharedPreferences("amigo_xiaomi_sync", Context.MODE_PRIVATE))
    private val json = Json { ignoreUnknownKeys = true }

    fun status(hasCredentials: Boolean) = XiaomiLocalStatus(
        enabled = values.getBoolean(KEY_ENABLED, false),
        hasCredentials = hasCredentials,
        status = values.getString(KEY_STATUS, "disabled") ?: "disabled",
        active = values.getBoolean(KEY_ACTIVE, false),
        region = values.getString(KEY_REGION, null),
        lastSync = instant(KEY_LAST_SYNC),
        dataAsOf = instant(KEY_DATA_AS_OF),
        lastErrorCode = values.getString(KEY_LAST_ERROR, null),
        completedTypes = XiaomiMetric.entries.count { historyEnd(it) != null },
        stepsAsOf = listOfNotNull(
            refreshEnd(XiaomiMetric.STEPS, XiaomiCursorLane.RECENT),
            refreshEnd(XiaomiMetric.STEPS),
        ).maxOrNull(),
        stepsPending = refreshCursor(XiaomiMetric.STEPS, XiaomiCursorLane.RECENT) != null ||
            refreshCursor(XiaomiMetric.STEPS) != null,
    )

    @Synchronized
    fun enable(accountFingerprint: String, region: String) {
        val existing = values.getString(KEY_ACCOUNT_FINGERPRINT, null)
        require(existing == null || existing == accountFingerprint) {
            "Сначала отключите текущий аккаунт Xiaomi"
        }
        values.edit()
            .putBoolean(KEY_ENABLED, true)
            .putString(KEY_ACCOUNT_FINGERPRINT, accountFingerprint)
            .putString(KEY_REGION, normalizeXiaomiRegion(region))
            .putString(KEY_STATUS, "pending")
            .remove(KEY_LAST_ERROR)
            .apply()
    }

    fun enabled(): Boolean = values.getBoolean(KEY_ENABLED, false)

    @Synchronized
    fun disable() {
        values.edit().clear().apply()
    }

    fun setServerState(status: String, active: Boolean, errorCode: String? = null) {
        values.edit()
            .putString(KEY_STATUS, status)
            .putBoolean(KEY_ACTIVE, active)
            .apply {
                if (errorCode == null) remove(KEY_LAST_ERROR) else putString(KEY_LAST_ERROR, errorCode)
            }
            .apply()
    }

    fun setRegion(region: String) {
        values.edit().putString(KEY_REGION, normalizeXiaomiRegion(region)).apply()
    }

    fun region(): String? = values.getString(KEY_REGION, null)

    fun refreshCursor(metric: XiaomiMetric, lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH): XiaomiCursor? {
        storedCursor(metricKey(metric, refreshKey(lane, "cursor")))?.let { return it }
        if (lane == XiaomiCursorLane.RECENT) return null
        val legacy = storedCursor(metricKey(metric, "cursor")) ?: return null
        return legacy.takeIf { classifyLegacyXiaomiCursor(historyEnd(metric), legacy) == XiaomiCursorLane.REFRESH }
    }

    fun historyCursor(metric: XiaomiMetric): XiaomiCursor? {
        val legacy = storedCursor(metricKey(metric, "cursor")) ?: return null
        return legacy.takeIf { classifyLegacyXiaomiCursor(historyEnd(metric), legacy) == XiaomiCursorLane.HISTORY }
    }

    /** Freeze the old lane assignment before the new recent lane seeds history. */
    fun migrateLegacyRefreshCursors() {
        val editor = values.edit()
        var changed = false
        XiaomiMetric.entries.forEach { metric ->
            val key = metricKey(metric, "cursor")
            val legacy = storedCursor(key) ?: return@forEach
            if (classifyLegacyXiaomiCursor(historyEnd(metric), legacy) == XiaomiCursorLane.REFRESH) {
                if (storedCursor(metricKey(metric, "refresh_cursor")) == null) {
                    editor.putString(metricKey(metric, "refresh_cursor"), values.getString(key, null))
                }
                editor.remove(key)
                changed = true
            }
        }
        if (changed) check(editor.commit()) { "Не удалось сохранить очереди синхронизации Xiaomi" }
    }

    private fun storedCursor(key: String): XiaomiCursor? {
        val raw = values.getString(key, null) ?: return null
        return runCatching {
            val item = json.parseToJsonElement(raw).jsonObject
            XiaomiCursor(
                snapshotId = item.required("snapshot_id"),
                rangeStart = Instant.parse(item.required("range_start")),
                rangeEnd = Instant.parse(item.required("range_end")),
                nextKey = item.optional("next_key"),
                pageIndex = item.required("page_index").toInt(),
                sourceDataAsOf = item.optional("source_data_as_of")?.let(Instant::parse),
                seenRecordHashes = (item["seen_record_hashes"] as? JsonArray)
                    ?.map { it.jsonPrimitive.content }
                    ?.onEach { require(it.matches(Regex("[0-9a-f]{64}"))) }
                    ?.toSet()
                    ?.also { require(it.size <= MAX_XIAOMI_SEEN_RECORD_HASHES) }
                    .orEmpty(),
            )
        }.getOrNull()
    }

    fun setRefreshCursor(
        metric: XiaomiMetric,
        cursor: XiaomiCursor,
        lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH,
    ) {
        val legacyIsRefresh = lane == XiaomiCursorLane.REFRESH && storedCursor(metricKey(metric, "cursor"))?.let {
            classifyLegacyXiaomiCursor(historyEnd(metric), it) == XiaomiCursorLane.REFRESH
        } == true
        values.edit()
            .putString(metricKey(metric, refreshKey(lane, "cursor")), encodeCursor(cursor))
            .apply {
                // Move a pre-1.4.1 initial/current cursor to the dedicated lane without
                // touching a simultaneously unfinished historical snapshot.
                if (legacyIsRefresh) remove(metricKey(metric, "cursor"))
            }
            .apply()
    }

    fun setHistoryCursor(metric: XiaomiMetric, cursor: XiaomiCursor) {
        values.edit()
            .putString(metricKey(metric, "cursor"), encodeCursor(cursor))
            .apply()
    }

    fun completeRefreshWindow(
        metric: XiaomiMetric,
        start: Instant,
        end: Instant,
        sourceDataAsOf: Instant?,
        lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH,
    ) {
        val currentHistoryEnd = historyEnd(metric)
        val legacyIsRefresh = lane == XiaomiCursorLane.REFRESH && storedCursor(metricKey(metric, "cursor"))?.let {
            classifyLegacyXiaomiCursor(currentHistoryEnd, it) == XiaomiCursorLane.REFRESH
        } == true
        values.edit()
            .remove(metricKey(metric, refreshKey(lane, "cursor")))
            .putString(metricKey(metric, refreshKey(lane, "start")), start.toString())
            .putString(metricKey(metric, refreshKey(lane, "end")), end.toString())
            .apply {
                if (legacyIsRefresh) remove(metricKey(metric, "cursor"))
                // The first recent snapshot seeds, but subsequent recent snapshots never
                // advance or rewind, the independent descending historical watermark.
                putString(
                    metricKey(metric, "history_end"),
                    xiaomiHistoryEndAfterRefresh(currentHistoryEnd, start).toString(),
                )
                updateDataAsOf(sourceDataAsOf)
            }
            .apply()
    }

    fun completeHistoryWindow(metric: XiaomiMetric, start: Instant, sourceDataAsOf: Instant?) {
        val nextHistoryEnd = earlierHistoryEnd(historyEnd(metric), start)
        values.edit()
            .remove(metricKey(metric, "cursor"))
            .putString(metricKey(metric, "history_end"), nextHistoryEnd.toString())
            .apply {
                updateDataAsOf(sourceDataAsOf)
            }
            .apply()
    }

    fun historyEnd(metric: XiaomiMetric): Instant? =
        instant(metricKey(metric, "history_end"))

    /** One atomic upgrade: re-read only exercise history, never pairing or other metrics. */
    @Synchronized
    fun prepareExerciseDetailsUpgrade(now: Instant) {
        if (values.getInt(KEY_EXERCISE_DETAILS_VERSION, 0) >= 1) return
        val metric = XiaomiMetric.EXERCISE
        val refresh = refreshCursor(metric)
        val editor = values.edit()
            .putInt(KEY_EXERCISE_DETAILS_VERSION, 1)
            .remove(metricKey(metric, "cursor"))
            .putString(metricKey(metric, "history_end"), now.toString())
        if (refresh != null) {
            // Retain the immutable round bounds, but discard old-format page state.
            editor.putString(metricKey(metric, "refresh_cursor"), encodeCursor(
                XiaomiCursor("mi-exercise-${UUID.randomUUID()}", refresh.rangeStart, refresh.rangeEnd),
            ))
        }
        check(editor.commit()) { "Не удалось сохранить обновление истории тренировок" }
    }

    fun refreshStart(metric: XiaomiMetric, lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH): Instant? =
        instant(metricKey(metric, refreshKey(lane, "start")))

    fun refreshEnd(metric: XiaomiMetric, lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH): Instant? =
        instant(metricKey(metric, refreshKey(lane, "end")))

    fun refreshRound(lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH): XiaomiRefreshRound? {
        val target = instant(refreshKey(lane, "round_target")) ?: return null
        val days = values.getLong(refreshKey(lane, "round_days"), 0L)
        return days.takeIf { it in 3L..30L }?.let { XiaomiRefreshRound(target, it) }
    }

    fun setRefreshRound(round: XiaomiRefreshRound, lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH) {
        require(round.days in 3L..30L)
        values.edit()
            .putString(refreshKey(lane, "round_target"), round.target.toString())
            .putLong(refreshKey(lane, "round_days"), round.days)
            .apply()
    }

    fun clearRefreshRound(round: XiaomiRefreshRound, lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH) {
        if (refreshRound(lane) != round) return
        values.edit()
            .remove(refreshKey(lane, "round_target"))
            .remove(refreshKey(lane, "round_days"))
            .apply()
    }

    fun dataAsOf(): Instant? = instant(KEY_DATA_AS_OF)

    fun nextMetricIndex(lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH): Int =
        values.getInt("${KEY_NEXT_METRIC}.${lane.name}", 0).mod(XiaomiMetric.entries.size)

    fun setNextMetricIndex(value: Int, lane: XiaomiCursorLane = XiaomiCursorLane.REFRESH) {
        values.edit().putInt("${KEY_NEXT_METRIC}.${lane.name}", value.mod(XiaomiMetric.entries.size)).apply()
    }

    fun pageTurn(): Int = values.getInt("page_turn", 0).mod(10)

    fun advancePageTurn() {
        values.edit().putInt("page_turn", (pageTurn() + 1).mod(10)).apply()
    }

    private fun refreshKey(lane: XiaomiCursorLane, suffix: String): String = when (lane) {
        XiaomiCursorLane.RECENT -> "recent_$suffix"
        XiaomiCursorLane.REFRESH -> "refresh_$suffix"
        XiaomiCursorLane.HISTORY -> error("History has no refresh round")
    }

    fun markSuccess(at: Instant, active: Boolean) {
        values.edit()
            .putString(KEY_LAST_SYNC, at.toString())
            .putString(KEY_STATUS, "success")
            .putBoolean(KEY_ACTIVE, active)
            .remove(KEY_LAST_ERROR)
            .apply()
    }

    fun regionDiscoveredFor(accountFingerprint: String): Boolean =
        values.getString(KEY_DISCOVERED_ACCOUNT, null) == accountFingerprint

    fun markRegionDiscovered(accountFingerprint: String, region: String) {
        values.edit()
            .putString(KEY_DISCOVERED_ACCOUNT, accountFingerprint)
            .putString(KEY_REGION, normalizeXiaomiRegion(region))
            .apply()
    }

    private fun instant(key: String): Instant? = values.getString(key, null)
        ?.let { runCatching { Instant.parse(it) }.getOrNull() }

    private fun metricKey(metric: XiaomiMetric, suffix: String) =
        "metric.${metric.type.wireName}.$suffix"

    private fun android.content.SharedPreferences.Editor.updateDataAsOf(sourceDataAsOf: Instant?) {
        val current = dataAsOf()
        if (sourceDataAsOf != null && (current == null || sourceDataAsOf > current)) {
            putString(KEY_DATA_AS_OF, sourceDataAsOf.toString())
        }
    }

    private fun encodeCursor(cursor: XiaomiCursor): String = CanonicalJson.render(buildJsonObject {
        cursor.nextKey?.let { put("next_key", it) }
        put("page_index", cursor.pageIndex)
        put("range_end", cursor.rangeEnd.toString())
        put("range_start", cursor.rangeStart.toString())
        put("snapshot_id", cursor.snapshotId)
        if (cursor.seenRecordHashes.isNotEmpty()) {
            put(
                "seen_record_hashes",
                JsonArray(cursor.seenRecordHashes.sorted().map(::JsonPrimitive)),
            )
        }
        cursor.sourceDataAsOf?.let { put("source_data_as_of", it.toString()) }
    })

    companion object {
        private const val KEY_ENABLED = "enabled"
        private const val KEY_ACCOUNT_FINGERPRINT = "account_fingerprint"
        private const val KEY_REGION = "region"
        private const val KEY_STATUS = "status"
        private const val KEY_ACTIVE = "active"
        private const val KEY_LAST_SYNC = "last_sync"
        private const val KEY_DATA_AS_OF = "data_as_of"
        private const val KEY_LAST_ERROR = "last_error_code"
        private const val KEY_NEXT_METRIC = "next_metric"
        private const val KEY_DISCOVERED_ACCOUNT = "region_discovered_account"
        private const val KEY_EXERCISE_DETAILS_VERSION = "exercise_details_version"
    }
}

internal const val MAX_XIAOMI_SEEN_RECORD_HASHES = 20_000

internal enum class XiaomiCursorLane {
    RECENT,
    REFRESH,
    HISTORY,
}

/** Classifies the single cursor key written by releases before refresh/history split. */
internal fun classifyLegacyXiaomiCursor(
    historyEnd: Instant?,
    cursor: XiaomiCursor,
): XiaomiCursorLane = if (historyEnd != null && cursor.rangeEnd == historyEnd) {
    XiaomiCursorLane.HISTORY
} else {
    XiaomiCursorLane.REFRESH
}

internal fun earlierHistoryEnd(current: Instant?, completedStart: Instant): Instant =
    current?.let { minOf(it, completedStart) } ?: completedStart

internal fun xiaomiHistoryEndAfterRefresh(current: Instant?, initialStart: Instant): Instant =
    current ?: initialStart

private fun kotlinx.serialization.json.JsonObject.required(key: String): String =
    getValue(key).jsonPrimitive.content

private fun kotlinx.serialization.json.JsonObject.optional(key: String): String? =
    get(key)?.jsonPrimitive?.content
