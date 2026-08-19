package ru.tolstik.amigo.sync.sync

import java.time.Instant
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import ru.tolstik.amigo.sync.wire.CanonicalJson

internal const val INGEST_BODY_LIMIT_BYTES = 1_048_576
internal const val MAX_RECORDS_PER_BATCH = 2_000
internal const val MAX_HEART_RATE_SAMPLES = 5_000
internal const val SNAPSHOT_CURSOR_FORMAT_VERSION = 2

/**
 * Converts a Health Connect page into deterministic server-sized batches.
 *
 * The Health Connect cursor is advanced only after every returned batch is acknowledged. If an
 * upload fails halfway through, reading the same source page produces the same batch IDs and raw
 * JSON, so already accepted batches can be replayed idempotently.
 */
class BatchPlanner(
    private val maxBodyBytesExclusive: Int = INGEST_BODY_LIMIT_BYTES,
    private val maxRecordsPerBatch: Int = MAX_RECORDS_PER_BATCH,
    private val maxHeartRateSamples: Int = MAX_HEART_RATE_SAMPLES,
) {
    init {
        require(maxBodyBytesExclusive > 1)
        require(maxRecordsPerBatch > 0)
        require(maxHeartRateSamples > 0)
    }

    fun snapshot(
        type: RecordType,
        origin: String,
        sourceRecords: List<ExportRecord>,
        rangeStart: Instant,
        rangeEnd: Instant,
        snapshotId: String,
        firstPageIndex: Int,
        sourceFinalPage: Boolean,
    ): List<BatchEnvelope> = partition(sourceRecords) { records, chunkIndex, isLastChunk ->
        val pageIndex = firstPageIndex + chunkIndex
        BatchEnvelope(
            batchId = stableId(snapshotId, pageIndex.toString()),
            mode = BatchMode.SNAPSHOT,
            recordType = type,
            dataOrigin = origin,
            dataAsOf = records.mapNotNull(ExportRecord::dataAsOf).maxOrNull() ?: rangeEnd,
            records = records,
            rangeStart = rangeStart,
            rangeEnd = rangeEnd,
            snapshotId = snapshotId,
            pageIndex = pageIndex,
            finalPage = sourceFinalPage && isLastChunk,
        )
    }

    fun changes(
        type: RecordType,
        origin: String,
        sourceToken: String,
        sourceRecords: List<ExportRecord>,
    ): List<BatchEnvelope> {
        if (sourceRecords.isEmpty()) return emptyList()
        return partition(sourceRecords) { records, chunkIndex, _ ->
            BatchEnvelope(
                batchId = stableId("changes-v2", type.wireName, sourceToken, chunkIndex.toString()),
                mode = BatchMode.CHANGES,
                recordType = type,
                dataOrigin = origin,
                // A deletion has no provider timestamp. EPOCH keeps retry JSON byte-identical.
                dataAsOf = records.mapNotNull(ExportRecord::dataAsOf).maxOrNull() ?: Instant.EPOCH,
                records = records,
            )
        }
    }

    private fun partition(
        sourceRecords: List<ExportRecord>,
        envelope: (records: List<ExportRecord>, chunkIndex: Int, isLastChunk: Boolean) -> BatchEnvelope,
    ): List<BatchEnvelope> {
        val records = sourceRecords
            .map { it.withBoundedHeartRateSamples(maxHeartRateSamples) }
            .sortedBy(ExportRecord::recordId)
        if (records.isEmpty()) {
            return listOf(envelope(emptyList(), 0, true).also(::requireFits))
        }

        val groups = mutableListOf<List<ExportRecord>>()
        var current = mutableListOf<ExportRecord>()
        records.forEach { record ->
            val candidate = current + record
            val candidateFits = candidate.size <= maxRecordsPerBatch &&
                encodedSize(envelope(candidate, groups.size, false)) < maxBodyBytesExclusive
            if (candidateFits) {
                current += record
            } else {
                require(current.isNotEmpty()) {
                    "A single Health Connect record cannot fit into an ingest batch"
                }
                groups += current.toList()
                current = mutableListOf(record)
                requireFits(envelope(current, groups.size, false))
            }
        }
        groups += current.toList()

        return groups.mapIndexed { index, group ->
            envelope(group, index, index == groups.lastIndex).also(::requireFits)
        }
    }

    private fun requireFits(batch: BatchEnvelope) {
        require(batch.records.size <= maxRecordsPerBatch) { "Ingest batch contains too many records" }
        require(encodedSize(batch) < maxBodyBytesExclusive) { "Ingest batch body is too large" }
    }

    internal fun encodedSize(batch: BatchEnvelope): Int = CanonicalJson.encode(batch.toJson()).size
}

private fun ExportRecord.withBoundedHeartRateSamples(limit: Int): ExportRecord {
    if (type != RecordType.HEART_RATE) return this
    val samples = values["samples"] as? JsonArray ?: return this
    if (samples.size <= limit) return this
    val lastIndex = samples.lastIndex.toLong()
    val selected = List(limit) { index ->
        val sourceIndex = if (limit == 1) {
            0
        } else {
            (index.toLong() * lastIndex / (limit - 1).toLong()).toInt()
        }
        samples[sourceIndex]
    }
    return copy(values = JsonObject(values + ("samples" to JsonArray(selected))))
}
