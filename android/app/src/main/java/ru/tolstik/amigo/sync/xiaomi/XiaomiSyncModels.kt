package ru.tolstik.amigo.sync.xiaomi

import java.time.Instant
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import ru.tolstik.amigo.sync.sync.ExportRecord
import ru.tolstik.amigo.sync.sync.RecordType

data class XiaomiBatchEnvelope(
    val batchId: String,
    val recordType: RecordType,
    val dataAsOf: Instant,
    val sourceDataAsOf: Instant?,
    val rangeStart: Instant,
    val rangeEnd: Instant,
    val snapshotId: String,
    val pageIndex: Int,
    val finalPage: Boolean,
    val records: List<ExportRecord>,
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("batch_id", batchId)
        put("data_as_of", dataAsOf.toString())
        put("final_page", finalPage)
        put("page_index", pageIndex)
        put("range_end", rangeEnd.toString())
        put("range_start", rangeStart.toString())
        put("record_type", recordType.wireName)
        put("records", JsonArray(records.sortedBy(ExportRecord::recordId).map(ExportRecord::toJson)))
        put("schema_version", 1)
        put("snapshot_id", snapshotId)
        sourceDataAsOf?.let { put("source_data_as_of", it.toString()) }
    }

    internal fun identityJson(): JsonObject = JsonObject(
        toJson().filterKeys { it != "batch_id" },
    )
}

data class XiaomiStatusReport(
    val reportId: String,
    val enabled: Boolean,
    val status: String,
    val accountFingerprint: String? = null,
    val region: String? = null,
    val dataAsOf: Instant? = null,
    val errorCode: String? = null,
) {
    fun toJson(): JsonObject = buildJsonObject {
        accountFingerprint?.let { put("account_fingerprint", it) }
        dataAsOf?.let { put("data_as_of", it.toString()) }
        put("enabled", enabled)
        errorCode?.let { put("error_code", it) }
        region?.let { put("region", it) }
        put("schema_version", 1)
        put("status", status)
    }
}

internal data class XiaomiCursor(
    val snapshotId: String,
    val rangeStart: Instant,
    val rangeEnd: Instant,
    val nextKey: String? = null,
    val pageIndex: Int = 0,
    val sourceDataAsOf: Instant? = null,
    val seenRecordHashes: Set<String> = emptySet(),
)

internal enum class XiaomiSyncMode {
    /** Start a recent reconciliation when the previous one is stale or too narrow. */
    ROUTINE,

    /** Finish required recent work, but otherwise spend the run on historical backfill. */
    BACKFILL_CONTINUATION,

    /** Reconcile the requested recent window even when the last routine run is still fresh. */
    FORCE_REFRESH,
}

internal data class XiaomiRefreshRound(
    val target: Instant,
    val days: Long,
)

data class XiaomiSyncSummary(
    val uploadedBatches: Int,
    val completedTypes: Int,
    val active: Boolean,
    val needsContinuation: Boolean,
)
