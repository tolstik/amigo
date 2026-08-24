package ru.tolstik.amigo.sync.xiaomi

import java.time.Instant
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.tolstik.amigo.sync.sync.ExportRecord

class XiaomiParsersTest {
    private val start = Instant.ofEpochSecond(1_700_000_000L)
    private val end = start.plusSeconds(86_400)

    @Test
    fun heartRateIsHourlyAggregateAndNeverContainsRawSamples() {
        val first = start.epochSecond + 100
        val records = XiaomiParsers.records(
            XiaomiMetric.HEART_RATE,
            listOf(
                XiaomiRawEntry("heart_rate", first, """{"time":$first,"bpm":60,"timezone":12}"""),
                XiaomiRawEntry("heart_rate", first + 10, """{"time":${first + 10},"bpm":90}"""),
            ),
            start,
            end,
        )
        assertEquals(1, records.size)
        val values = records.single().values
        assertEquals("75.0", values.getValue("average_bpm").jsonPrimitive.content)
        assertEquals("60", values.getValue("minimum_bpm").jsonPrimitive.content)
        assertEquals("90", values.getValue("maximum_bpm").jsonPrimitive.content)
        assertEquals("2", values.getValue("sample_count").jsonPrimitive.content)
        assertFalse("samples" in values)
    }

    @Test
    fun sleepMappingUsesStableUpstreamCodesIncludingBothAwakeCodes() {
        val bed = start.epochSecond + 100
        val wake = bed + 500
        val records = XiaomiParsers.records(
            XiaomiMetric.SLEEP,
            listOf(
                XiaomiRawEntry(
                    "sleep",
                    wake,
                    """{"bedtime":$bed,"wake_up_time":$wake,"items":[""" +
                        """{"start_time":$bed,"end_time":${bed + 100},"state":1},""" +
                        """{"start_time":${bed + 100},"end_time":${bed + 200},"state":2},""" +
                        """{"start_time":${bed + 200},"end_time":${bed + 300},"state":3},""" +
                        """{"start_time":${bed + 300},"end_time":${bed + 400},"state":4},""" +
                        """{"start_time":${bed + 400},"end_time":$wake,"state":5}]}""",
                ),
            ),
            start,
            end,
        )
        val stages = records.single().values.getValue("stages").jsonArray
            .map { it.jsonObject.getValue("stage").jsonPrimitive.content }
        assertEquals(listOf("awake", "deep", "light", "rem", "awake"), stages)
    }

    @Test
    fun onlyAllowlistedCloudMetricsExist() {
        val names = XiaomiMetric.entries.map { it.type.wireName }.toSet()
        assertFalse("weight" in names)
        assertFalse("blood_pressure" in names)
        assertFalse("total_calories" in names)
        assertFalse("route" in names)
        assertEquals(10, names.size)
    }

    @Test
    fun directCloudFixturesMapEveryAllowlistedMetricWithoutRoutes() {
        val at = start.epochSecond + 3_600
        val steps = XiaomiRawEntry(
            "steps",
            at,
            """{"time":$at,"steps":120,"distance":87.5,"timezone":12}""",
        )
        val fixtures = mapOf(
            XiaomiMetric.STEPS to listOf(steps),
            XiaomiMetric.DISTANCE to listOf(steps),
            XiaomiMetric.ACTIVE_CALORIES to listOf(
                XiaomiRawEntry("calories", at, """{"time":$at,"calories":12.5}"""),
            ),
            XiaomiMetric.RESTING_HEART_RATE to listOf(
                XiaomiRawEntry("resting_heart_rate", at, """{"date_time":$at,"bpm":61}"""),
            ),
            XiaomiMetric.OXYGEN_SATURATION to listOf(
                XiaomiRawEntry("spo2", at, """{"time":$at,"spo2":97}"""),
            ),
            XiaomiMetric.VO2_MAX to listOf(
                XiaomiRawEntry("vo2_max", at, """{"time":$at,"vo2_max":41.2}"""),
            ),
            XiaomiMetric.HRV_RMSSD to listOf(
                XiaomiRawEntry(
                    "sleep",
                    at,
                    """{"wake_up_time":$at,"avg_hrv":48,"hrv_analysis_timestamp":$at}""",
                ),
            ),
            XiaomiMetric.EXERCISE to listOf(
                XiaomiRawEntry(
                    "sport",
                    at,
                    """{"start_time":$at,"duration":1800,"distance":5000,"route":[1,2]}""",
                    category = "outdoor running",
                ),
            ),
        )

        fixtures.forEach { (metric, entries) ->
            val records = XiaomiParsers.records(metric, entries, start, end)
            assertEquals(metric.name, 1, records.size)
            assertFalse(metric.name, "route" in records.single().values)
            assertFalse(metric.name, "samples" in records.single().values)
        }
        assertEquals(
            "outdoor_running",
            XiaomiParsers.records(XiaomiMetric.EXERCISE, fixtures.getValue(XiaomiMetric.EXERCISE), start, end)
                .single().values.getValue("exercise_type").jsonPrimitive.content,
        )
    }

    @Test
    fun plannerKeepsRecordAndBodyLimits() {
        val records = (0..2_000).map { index ->
            ExportRecord(
                recordId = "mi-steps-$index",
                type = XiaomiMetric.STEPS.type,
                startTime = start.plusSeconds(index.toLong()),
                endTime = start.plusSeconds(index.toLong()),
                dataOrigin = "xiaomi_cloud",
                values = kotlinx.serialization.json.buildJsonObject { put("count", 1) },
            )
        }
        val envelopes = XiaomiBatchPlanner.plan(
            metric = XiaomiMetric.STEPS,
            records = records,
            rangeStart = start,
            rangeEnd = end,
            snapshotId = "snapshot-test",
            firstPageIndex = 0,
            sourceFinalPage = true,
            sourceDataAsOf = end.minusSeconds(1),
            now = end,
        )
        assertEquals(2, envelopes.size)
        assertTrue(envelopes.all { it.records.size <= 2_000 })
        assertTrue(envelopes.dropLast(1).none { it.finalPage })
        assertTrue(envelopes.last().finalPage)
        assertTrue(envelopes.all {
            ru.tolstik.amigo.sync.wire.CanonicalJson.encode(it.toJson()).size < 1_048_576
        })
    }
}
