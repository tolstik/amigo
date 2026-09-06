package ru.tolstik.amigo.sync.xiaomi

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put

/** Verified SportBasicReport/FitnessSportType mappings; see docs/xiaomi-swimming.md. */
internal object XiaomiSwimming {
    fun isPool(report: JsonObject, label: String): Boolean {
        val sportType = (report["sport_type"] as? JsonPrimitive)?.intOrNull
        if ("sport_type" in report) return sportType == 9
        return label in setOf("pool_swimming", "swimming_pool", "indoor_swimming")
    }

    fun details(report: JsonObject, elapsedSeconds: Long): JsonObject = buildJsonObject {
        fun bounded(key: String, low: Double, high: Double): Double? =
            (report[key] as? JsonPrimitive)?.doubleOrNull?.takeIf { it.isFinite() && it in low..high }

        bounded("distance", 0.0, 1_000_000.0)?.let { put("distance_meters", it) }
        bounded("valid_duration", 0.0, elapsedSeconds.toDouble())?.let { put("active_duration_seconds", it) }
        bounded("calories", 0.0, 100_000.0)?.let { put("kilocalories", it) }
        bounded("pool_width", 1.0, 200.0)?.let { put("pool_length_meters", it) }
        // Xiaomi report field 39 is the pool-lap counter (Gadgetbridge LAPS);
        // the Huami converter maps total_trips to it. Copy the counter exactly,
        // without adding a turn or deriving it from distance / pool_width.
        bounded("turn_count", 0.0, 100_000.0)?.takeIf { it % 1.0 == 0.0 }
            ?.let { put("pool_lengths", it.toInt()) }
        val minimum = bounded("min_hrm", 20.0, 300.0)
        val average = bounded("avg_hrm", 20.0, 300.0)
        val maximum = bounded("max_hrm", 20.0, 300.0)
        val heart = listOfNotNull(minimum, average, maximum)
        if (heart == heart.sorted()) {
            minimum?.let { put("minimum_bpm", it) }
            average?.let { put("average_bpm", it) }
            maximum?.let { put("maximum_bpm", it) }
        }
        val style = when ((report["main_posture"] as? JsonPrimitive)?.intOrNull) {
            0 -> "medley"
            1 -> "breaststroke"
            2 -> "freestyle"
            3 -> "backstroke"
            4 -> "butterfly"
            else -> null
        }
        style?.let { put("stroke_style", it) }
        // avg_pace is not copied: its sport-specific unit is ambiguous. The
        // server derives seconds / 100 m only from distance and valid_duration.
    }
}
