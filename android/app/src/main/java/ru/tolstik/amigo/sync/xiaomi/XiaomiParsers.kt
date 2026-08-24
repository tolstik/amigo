package ru.tolstik.amigo.sync.xiaomi

import java.security.MessageDigest
import java.time.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import ru.tolstik.amigo.sync.sync.ExportRecord
import ru.tolstik.amigo.sync.sync.RecordType

internal enum class XiaomiMetric(val type: RecordType, val cloudKey: String?) {
    STEPS(RecordType.STEPS, "steps"),
    DISTANCE(RecordType.DISTANCE, "steps"),
    ACTIVE_CALORIES(RecordType.ACTIVE_CALORIES, "calories"),
    EXERCISE(RecordType.EXERCISE, null),
    SLEEP(RecordType.SLEEP, "sleep"),
    HEART_RATE(RecordType.HEART_RATE, "heart_rate"),
    RESTING_HEART_RATE(RecordType.RESTING_HEART_RATE, "resting_heart_rate"),
    HRV_RMSSD(RecordType.HRV_RMSSD, "sleep"),
    OXYGEN_SATURATION(RecordType.OXYGEN_SATURATION, "spo2"),
    VO2_MAX(RecordType.VO2_MAX, "vo2_max"),
}

internal object XiaomiParsers {
    private val json = Json { ignoreUnknownKeys = true }

    fun records(
        metric: XiaomiMetric,
        entries: List<XiaomiRawEntry>,
        rangeStart: Instant,
        rangeEnd: Instant,
    ): List<ExportRecord> = when (metric) {
        XiaomiMetric.STEPS -> hourlyTotals(metric, entries, "steps", integer = true, rangeStart, rangeEnd)
        XiaomiMetric.DISTANCE -> hourlyTotals(metric, entries, "distance", integer = false, rangeStart, rangeEnd)
        XiaomiMetric.ACTIVE_CALORIES -> hourlyTotals(
            metric,
            entries,
            "calories",
            integer = false,
            rangeStart,
            rangeEnd,
        )
        XiaomiMetric.HEART_RATE -> hourlyHeartRate(entries, rangeStart, rangeEnd)
        XiaomiMetric.RESTING_HEART_RATE -> scalarSamples(
            metric,
            entries,
            listOf("bpm"),
            20.0..300.0,
            "bpm",
            rangeStart,
            rangeEnd,
            timestampKeys = listOf("date_time", "time"),
        )
        XiaomiMetric.OXYGEN_SATURATION -> scalarSamples(
            metric,
            entries,
            listOf("spo2"),
            0.0..100.0,
            "percentage",
            rangeStart,
            rangeEnd,
        )
        XiaomiMetric.VO2_MAX -> scalarSamples(
            metric,
            entries,
            listOf("vo2_max", "vo2Max"),
            0.01..100.0,
            "milliliters_per_minute_kilogram",
            rangeStart,
            rangeEnd,
        )
        XiaomiMetric.SLEEP -> sleepSessions(entries, rangeStart, rangeEnd)
        XiaomiMetric.HRV_RMSSD -> sleepHrv(entries, rangeStart, rangeEnd)
        XiaomiMetric.EXERCISE -> exercises(entries, rangeStart, rangeEnd)
    }.distinctBy(ExportRecord::recordId).sortedBy(ExportRecord::recordId)

    private fun hourlyTotals(
        metric: XiaomiMetric,
        entries: List<XiaomiRawEntry>,
        field: String,
        integer: Boolean,
        rangeStart: Instant,
        rangeEnd: Instant,
    ): List<ExportRecord> {
        data class Bucket(var total: Double = 0.0, var zoneOffset: Int? = null)
        val buckets = mutableMapOf<Long, Bucket>()
        entries.forEach { entry ->
            val value = parseObject(entry.value) ?: return@forEach
            val timestamp = value.long("time") ?: entry.time
            if (!inside(timestamp, rangeStart, rangeEnd)) return@forEach
            val amount = value.double(field) ?: return@forEach
            if (amount <= 0.0 || !amount.isFinite()) return@forEach
            val hour = timestamp / 3600 * 3600
            buckets.getOrPut(hour, ::Bucket).also { bucket ->
                bucket.total += amount
                if (bucket.zoneOffset == null) bucket.zoneOffset = zoneOffset(value)
            }
        }
        return buckets.mapNotNull { (hour, bucket) ->
            val total = if (integer) bucket.total.toLong().toDouble() else bucket.total
            if (total <= 0) return@mapNotNull null
            val valueName = when (metric) {
                XiaomiMetric.STEPS -> "count"
                XiaomiMetric.DISTANCE -> "meters"
                else -> "kilocalories"
            }
            record(
                metric,
                hour,
                hour + 3599,
                buildJsonObject {
                    put(valueName, if (integer) JsonPrimitive(total.toLong()) else JsonPrimitive(total))
                    bucket.zoneOffset?.let { put("zone_offset_seconds", it) }
                },
                suffix = hour.toString(),
            )
        }
    }

    private fun hourlyHeartRate(
        entries: List<XiaomiRawEntry>,
        rangeStart: Instant,
        rangeEnd: Instant,
    ): List<ExportRecord> {
        data class Bucket(val values: MutableList<Int> = mutableListOf(), var zoneOffset: Int? = null)
        val buckets = mutableMapOf<Long, Bucket>()
        entries.forEach { entry ->
            val value = parseObject(entry.value) ?: return@forEach
            val timestamp = value.long("time") ?: entry.time
            val bpm = value.int("bpm") ?: return@forEach
            if (bpm !in 20..300 || !inside(timestamp, rangeStart, rangeEnd)) return@forEach
            val hour = timestamp / 3600 * 3600
            buckets.getOrPut(hour, ::Bucket).also { bucket ->
                bucket.values += bpm
                if (bucket.zoneOffset == null) bucket.zoneOffset = zoneOffset(value)
            }
        }
        return buckets.map { (hour, bucket) ->
            record(
                XiaomiMetric.HEART_RATE,
                hour,
                hour + 3599,
                buildJsonObject {
                    put("average_bpm", bucket.values.average())
                    put("minimum_bpm", bucket.values.min())
                    put("maximum_bpm", bucket.values.max())
                    put("sample_count", bucket.values.size)
                    bucket.zoneOffset?.let { put("zone_offset_seconds", it) }
                },
                suffix = hour.toString(),
            )
        }
    }

    private fun scalarSamples(
        metric: XiaomiMetric,
        entries: List<XiaomiRawEntry>,
        fields: List<String>,
        range: ClosedFloatingPointRange<Double>,
        outputField: String,
        rangeStart: Instant,
        rangeEnd: Instant,
        timestampKeys: List<String> = listOf("time"),
    ): List<ExportRecord> = entries.mapNotNull { entry ->
        val value = parseObject(entry.value) ?: return@mapNotNull null
        val timestamp = timestampKeys.firstNotNullOfOrNull(value::long) ?: entry.time
        if (!inside(timestamp, rangeStart, rangeEnd)) return@mapNotNull null
        val measurement = fields.firstNotNullOfOrNull(value::double) ?: return@mapNotNull null
        if (measurement !in range || !measurement.isFinite()) return@mapNotNull null
        record(
            metric,
            timestamp,
            timestamp,
            buildJsonObject {
                put(outputField, measurement)
                zoneOffset(value)?.let { put("zone_offset_seconds", it) }
            },
            suffix = "$timestamp-${shortHash(entry.value)}",
        )
    }

    private fun sleepSessions(
        entries: List<XiaomiRawEntry>,
        rangeStart: Instant,
        rangeEnd: Instant,
    ): List<ExportRecord> = entries.mapNotNull { entry ->
        val value = parseObject(entry.value) ?: return@mapNotNull null
        val start = value.long("bedtime") ?: entry.time
        val end = value.long("wake_up_time") ?: entry.time
        if (start <= 0 || end <= start || !overlaps(start, end, rangeStart, rangeEnd)) {
            return@mapNotNull null
        }
        val stages = value["items"]?.jsonArray.orEmpty().mapNotNull { item ->
            val stage = runCatching { item.jsonObject }.getOrNull() ?: return@mapNotNull null
            val stageStart = stage.long("start_time") ?: return@mapNotNull null
            val stageEnd = stage.long("end_time") ?: return@mapNotNull null
            val name = sleepStage(stage.int("state")) ?: return@mapNotNull null
            if (stageEnd < stageStart || stageStart < start || stageEnd > end) return@mapNotNull null
            buildJsonObject {
                put("end_time", Instant.ofEpochSecond(stageEnd).toString())
                put("stage", name)
                put("start_time", Instant.ofEpochSecond(stageStart).toString())
            }
        }.take(500)
        record(
            XiaomiMetric.SLEEP,
            start,
            end,
            buildJsonObject {
                put("duration_seconds", end - start)
                put("stages", JsonArray(stages))
                zoneOffset(value)?.let { put("zone_offset_seconds", it) }
            },
            suffix = "$start-${shortHash(entry.value)}",
        )
    }

    private fun sleepHrv(
        entries: List<XiaomiRawEntry>,
        rangeStart: Instant,
        rangeEnd: Instant,
    ): List<ExportRecord> = entries.mapNotNull { entry ->
        val value = parseObject(entry.value) ?: return@mapNotNull null
        val hrv = value.double("avg_hrv") ?: value.double("avgHrv") ?: return@mapNotNull null
        val timestamp = value.long("hrv_analysis_timestamp")
            ?: value.long("hrvAnalysisTimestamp")
            ?: value.long("wake_up_time")
            ?: entry.time
        if (hrv !in 5.0..300.0 || !inside(timestamp, rangeStart, rangeEnd)) return@mapNotNull null
        record(
            XiaomiMetric.HRV_RMSSD,
            timestamp,
            timestamp,
            buildJsonObject {
                put("milliseconds", hrv)
                zoneOffset(value)?.let { put("zone_offset_seconds", it) }
            },
            suffix = "$timestamp-${shortHash(entry.value)}",
        )
    }

    private fun exercises(
        entries: List<XiaomiRawEntry>,
        rangeStart: Instant,
        rangeEnd: Instant,
    ): List<ExportRecord> = entries.mapNotNull { entry ->
        val value = parseObject(entry.value) ?: return@mapNotNull null
        val start = value.long("start_time") ?: entry.time
        val duration = value.long("duration")
        val end = value.long("end_time") ?: duration?.let(start::plus) ?: return@mapNotNull null
        if (end <= start || end - start > 604_800 || !overlaps(start, end, rangeStart, rangeEnd)) {
            return@mapNotNull null
        }
        val rawType = entry.category
            ?: entry.key.takeIf { it.isNotBlank() && it != "sport" }
            ?: value.string("sport_type")
            ?: value.string("type")
            ?: "workout"
        val safeType = rawType.lowercase().replace(Regex("[^a-z0-9_-]+"), "_")
            .trim('_').take(64).ifBlank { "workout" }
        record(
            XiaomiMetric.EXERCISE,
            start,
            end,
            buildJsonObject {
                put("duration_seconds", end - start)
                put("exercise_type", safeType)
                zoneOffset(value)?.let { put("zone_offset_seconds", it) }
            },
            suffix = "$start-${shortHash(entry.value)}",
        )
    }

    private fun record(
        metric: XiaomiMetric,
        start: Long,
        end: Long,
        values: JsonObject,
        suffix: String,
    ) = ExportRecord(
        recordId = "mi-${metric.type.wireName}-$suffix",
        type = metric.type,
        startTime = Instant.ofEpochSecond(start),
        endTime = Instant.ofEpochSecond(end),
        dataOrigin = "xiaomi_cloud",
        values = values,
    )

    private fun sleepStage(code: Int?): String? = when (code) {
        1, 5 -> "awake"
        2 -> "deep"
        3 -> "light"
        4 -> "rem"
        else -> null
    }

    private fun parseObject(raw: String): JsonObject? =
        runCatching { json.parseToJsonElement(raw).jsonObject }.getOrNull()

    private fun zoneOffset(value: JsonObject): Int? = value.int("timezone")
        ?.takeIf { it in -72..72 }
        ?.times(900)

    private fun inside(value: Long, start: Instant, end: Instant): Boolean =
        value >= start.epochSecond && value < end.epochSecond

    private fun overlaps(start: Long, end: Long, rangeStart: Instant, rangeEnd: Instant): Boolean =
        start < rangeEnd.epochSecond && end >= rangeStart.epochSecond

    private fun shortHash(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .take(8)
        .joinToString("") { "%02x".format(it) }
}

private fun JsonObject.long(key: String): Long? =
    get(key)?.jsonPrimitive?.longOrNull

private fun JsonObject.int(key: String): Int? =
    get(key)?.jsonPrimitive?.intOrNull

private fun JsonObject.double(key: String): Double? =
    get(key)?.jsonPrimitive?.doubleOrNull
        ?: get(key)?.jsonPrimitive?.content?.toDoubleOrNull()

private fun JsonObject.string(key: String): String? =
    get(key)?.jsonPrimitive?.content?.takeIf(String::isNotBlank)
