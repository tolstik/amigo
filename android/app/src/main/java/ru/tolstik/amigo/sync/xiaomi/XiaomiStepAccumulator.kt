package ru.tolstik.amigo.sync.xiaomi

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.security.MessageDigest
import java.time.Instant
import java.util.Base64
import java.util.zip.GZIPInputStream
import java.util.zip.GZIPOutputStream
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import kotlinx.serialization.json.buildJsonObject
import ru.tolstik.amigo.sync.sync.ExportRecord
import ru.tolstik.amigo.sync.sync.RecordType

/** Locally reconciles all provider pages before publishing one immutable step snapshot. */
internal class XiaomiStepAccumulator private constructor(
    private val samples: MutableMap<SampleKey, SampleValue>,
    private val requireSource: Boolean,
) {
    private data class SampleKey(val source: Long, val time: Long)
    private data class SampleValue(val steps: Int, val zoneOffsetSeconds: Int?)
    private val json = Json { ignoreUnknownKeys = true }
    private val sourceHashes = mutableMapOf<String, Long>()

    fun add(entries: List<XiaomiRawEntry>, start: Instant, end: Instant) {
        entries.forEach { entry ->
            if (entry.key.isNotBlank() && entry.key != "steps") return@forEach
            if (requireSource) check(!entry.sid.isNullOrBlank()) {
                "Xiaomi step record has no source identifier"
            }
            val value = runCatching { json.parseToJsonElement(entry.value).jsonObject }.getOrNull()
                ?: return@forEach
            val time = value["time"]?.jsonPrimitive?.longOrNull ?: entry.time
            if (time < start.epochSecond || time >= end.epochSecond) return@forEach
            val steps = value["steps"]?.jsonPrimitive?.intOrNull ?: return@forEach
            if (steps !in 1..1_000_000) return@forEach
            val source = sourceHashes.getOrPut(entry.sid.orEmpty()) {
                val bytes = MessageDigest.getInstance("SHA-256")
                    .digest(entry.sid.orEmpty().toByteArray(Charsets.UTF_8))
                java.nio.ByteBuffer.wrap(bytes).long
            }
            val offset = value["timezone"]?.jsonPrimitive?.intOrNull
                ?.takeIf { it in -72..72 }?.times(900)
            val key = SampleKey(source, time)
            val previous = samples[key]
            if (previous == null || steps > previous.steps) {
                check(previous != null || samples.size < MAX_SAMPLES) {
                    "Xiaomi step snapshot exceeds local sample limit"
                }
                samples[key] = SampleValue(steps, offset)
            }
        }
    }

    fun records(): List<ExportRecord> {
        data class HourSource(var total: Long = 0, var zoneOffset: Int? = null)
        val byHourSource = mutableMapOf<Pair<Long, Long>, HourSource>()
        samples.forEach { (key, sample) ->
            val hour = Math.floorDiv(key.time, 3600) * 3600
            byHourSource.getOrPut(hour to key.source, ::HourSource).also {
                it.total += sample.steps
                if (it.zoneOffset == null) it.zoneOffset = sample.zoneOffsetSeconds
            }
        }
        return byHourSource.entries.groupBy { it.key.first }.mapNotNull { (hour, streams) ->
            // The phone and watch can both upload the same activity. Mi Fitness
            // reconciles sources; summing every sid nearly doubles the result.
            // One source per hour is conservative when their time bins differ.
            val selected = streams.maxWithOrNull(
                compareBy<Map.Entry<Pair<Long, Long>, HourSource>> { it.value.total }
                    .thenBy { it.key.second },
            )?.value ?: return@mapNotNull null
            check(selected.total <= 1_000_000) {
                "Xiaomi hourly step count exceeds ingest limit"
            }
            ExportRecord(
                recordId = "mi-steps-$hour",
                type = RecordType.STEPS,
                startTime = Instant.ofEpochSecond(hour),
                endTime = Instant.ofEpochSecond(hour + 3599),
                dataOrigin = "xiaomi_cloud",
                values = buildJsonObject {
                    put("count", selected.total)
                    selected.zoneOffset?.let { put("zone_offset_seconds", it) }
                },
            )
        }.sortedBy(ExportRecord::recordId)
    }

    fun encode(): String {
        if (samples.isEmpty()) return ""
        val bytes = ByteArrayOutputStream()
        GZIPOutputStream(bytes).use { zip ->
            DataOutputStream(zip).use { output ->
                output.writeInt(FORMAT_VERSION)
                output.writeInt(samples.size)
                samples.toSortedMap(compareBy<SampleKey> { it.source }.thenBy { it.time })
                    .forEach { (key, value) ->
                        output.writeLong(key.source)
                        output.writeLong(key.time)
                        output.writeInt(value.steps)
                        output.writeInt(value.zoneOffsetSeconds ?: Int.MIN_VALUE)
                    }
            }
        }
        val encoded = Base64.getEncoder().encodeToString(bytes.toByteArray())
        check(encoded.length <= MAX_ENCODED_LENGTH) { "Xiaomi step cursor exceeds storage limit" }
        return encoded
    }

    companion object {
        private const val FORMAT_VERSION = 1
        private const val MAX_SAMPLES = 200_000
        private const val MAX_ENCODED_LENGTH = 8_000_000

        fun empty(requireSource: Boolean = true) =
            XiaomiStepAccumulator(mutableMapOf(), requireSource)

        fun decode(encoded: String): XiaomiStepAccumulator {
            if (encoded.isEmpty()) return empty()
            require(encoded.length <= MAX_ENCODED_LENGTH)
            val samples = mutableMapOf<SampleKey, SampleValue>()
            DataInputStream(GZIPInputStream(ByteArrayInputStream(Base64.getDecoder().decode(encoded)))).use { input ->
                require(input.readInt() == FORMAT_VERSION)
                val count = input.readInt()
                require(count in 0..MAX_SAMPLES)
                repeat(count) {
                    val key = SampleKey(input.readLong(), input.readLong())
                    val steps = input.readInt()
                    val offset = input.readInt().takeUnless { it == Int.MIN_VALUE }
                    require(steps in 1..1_000_000)
                    require(offset == null || offset in -64_800..64_800)
                    require(samples.put(key, SampleValue(steps, offset)) == null)
                }
                require(input.read() == -1)
            }
            return XiaomiStepAccumulator(samples, requireSource = true)
        }
    }
}
