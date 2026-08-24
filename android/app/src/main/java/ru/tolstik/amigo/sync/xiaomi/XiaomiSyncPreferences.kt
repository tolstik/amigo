package ru.tolstik.amigo.sync.xiaomi

import android.content.Context
import java.time.Instant
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
)

internal class XiaomiSyncPreferences(context: Context) {
    private val values = context.getSharedPreferences("amigo_xiaomi_sync", Context.MODE_PRIVATE)
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

    fun cursor(metric: XiaomiMetric): XiaomiCursor? {
        val raw = values.getString(metricKey(metric, "cursor"), null) ?: return null
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

    fun setCursor(metric: XiaomiMetric, cursor: XiaomiCursor) {
        values.edit().putString(
            metricKey(metric, "cursor"),
            CanonicalJson.render(buildJsonObject {
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
            }),
        ).apply()
    }

    fun completeWindow(metric: XiaomiMetric, start: Instant, sourceDataAsOf: Instant?) {
        val nextHistoryEnd = earlierHistoryEnd(historyEnd(metric), start)
        values.edit()
            .remove(metricKey(metric, "cursor"))
            .putString(metricKey(metric, "history_end"), nextHistoryEnd.toString())
            .apply {
                val current = dataAsOf()
                if (sourceDataAsOf != null && (current == null || sourceDataAsOf > current)) {
                    putString(KEY_DATA_AS_OF, sourceDataAsOf.toString())
                }
            }
            .apply()
    }

    fun historyEnd(metric: XiaomiMetric): Instant? =
        instant(metricKey(metric, "history_end"))

    fun dataAsOf(): Instant? = instant(KEY_DATA_AS_OF)

    fun nextMetricIndex(): Int = values.getInt(KEY_NEXT_METRIC, 0)

    fun setNextMetricIndex(value: Int) {
        values.edit().putInt(KEY_NEXT_METRIC, value.mod(XiaomiMetric.entries.size)).apply()
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
    }
}

internal const val MAX_XIAOMI_SEEN_RECORD_HASHES = 20_000

internal fun earlierHistoryEnd(current: Instant?, completedStart: Instant): Instant =
    current?.let { minOf(it, completedStart) } ?: completedStart

private fun kotlinx.serialization.json.JsonObject.required(key: String): String =
    getValue(key).jsonPrimitive.content

private fun kotlinx.serialization.json.JsonObject.optional(key: String): String? =
    get(key)?.jsonPrimitive?.content
