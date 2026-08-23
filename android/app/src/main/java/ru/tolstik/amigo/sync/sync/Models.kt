package ru.tolstik.amigo.sync.sync

import java.time.Instant
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

enum class RecordType(val wireName: String) {
    STEPS("steps"),
    DISTANCE("distance"),
    ACTIVE_CALORIES("active_calories"),
    TOTAL_CALORIES("total_calories"),
    EXERCISE("exercise"),
    SLEEP("sleep"),
    HEART_RATE("heart_rate"),
    RESTING_HEART_RATE("resting_heart_rate"),
    HRV_RMSSD("hrv_rmssd"),
    OXYGEN_SATURATION("oxygen_saturation"),
    VO2_MAX("vo2_max"),
}

data class ExportRecord(
    val recordId: String,
    val type: RecordType,
    val startTime: Instant? = null,
    val endTime: Instant? = null,
    val dataOrigin: String,
    val lastModifiedTime: Instant? = null,
    val values: JsonObject = JsonObject(emptyMap()),
    val deleted: Boolean = false,
) {
    init {
        require(recordId.isNotBlank())
        require(dataOrigin.isNotBlank())
        if (!deleted) require(startTime != null)
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("data_origin", dataOrigin)
        put("deleted", deleted)
        endTime?.let { put("end_time", it.toString()) }
        lastModifiedTime?.let { put("last_modified_time", it.toString()) }
        put("record_id", recordId)
        startTime?.let { put("start_time", it.toString()) }
        put("type", type.wireName)
        put("values", values)
    }

    val dataAsOf: Instant?
        // Mi Fitness may publish an in-progress interval whose end boundary is still
        // in the future. The Health Connect modification time is the source watermark;
        // treating the interval end as freshness makes a valid current record look
        // future-dated and can stall every later metric in the ordered sync.
        get() = lastModifiedTime ?: endTime ?: startTime
}

enum class BatchMode(val wireName: String) {
    SNAPSHOT("snapshot"),
    CHANGES("changes"),
}

data class BatchEnvelope(
    val batchId: String,
    val mode: BatchMode,
    val recordType: RecordType,
    val dataOrigin: String,
    val dataAsOf: Instant,
    val records: List<ExportRecord>,
    val rangeStart: Instant? = null,
    val rangeEnd: Instant? = null,
    val snapshotId: String? = null,
    val pageIndex: Int? = null,
    val finalPage: Boolean? = null,
) {
    init {
        require(batchId.isNotBlank())
        require(dataOrigin.isNotBlank())
        require(records.all { it.type == recordType && it.dataOrigin == dataOrigin })
        if (mode == BatchMode.SNAPSHOT) {
            require(rangeStart != null && rangeEnd != null && rangeStart < rangeEnd)
            require(!snapshotId.isNullOrBlank())
            require(pageIndex != null && pageIndex >= 0)
            require(finalPage != null)
        }
    }

    fun toJson(): JsonObject = buildJsonObject {
        put("batch_id", batchId)
        put("data_as_of", dataAsOf.toString())
        put("data_origin", dataOrigin)
        finalPage?.let { put("final_page", it) }
        put("mode", mode.wireName)
        pageIndex?.let { put("page_index", it) }
        rangeEnd?.let { put("range_end", it.toString()) }
        rangeStart?.let { put("range_start", it.toString()) }
        put(
            "records",
            JsonArray(records.sortedBy { it.recordId }.map(ExportRecord::toJson)),
        )
        put("record_type", recordType.wireName)
        put("schema_version", 1)
        snapshotId?.let { put("snapshot_id", it) }
    }
}

data class SnapshotPage(
    val records: List<ExportRecord>,
    val nextPageToken: String?,
)

sealed interface ExportChange {
    data class Upsert(val record: ExportRecord) : ExportChange

    data class Delete(val recordId: String) : ExportChange
}

data class ChangePage(
    val changes: List<ExportChange>,
    val nextChangesToken: String,
    val hasMore: Boolean,
    val tokenExpired: Boolean,
)

data class SnapshotCursor(
    val generation: String,
    val target: Instant,
    val rangeStart: Instant,
    val rangeEnd: Instant,
    val pageToken: String? = null,
    val pageIndex: Int = 0,
    val knownEmpty: Boolean = false,
    val emptyUntil: Instant? = null,
    val formatVersion: Int = SNAPSHOT_CURSOR_FORMAT_VERSION,
)

data class SyncSummary(
    val uploadedBatches: Int,
    val completedTypes: Int,
    val dataAsOf: Instant?,
)

internal fun jsonString(value: String?) = value?.let(::JsonPrimitive) ?: JsonNull
