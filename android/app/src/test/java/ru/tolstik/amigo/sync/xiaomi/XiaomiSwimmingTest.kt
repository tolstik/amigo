package ru.tolstik.amigo.sync.xiaomi

import java.time.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class XiaomiSwimmingTest {
    private val start = Instant.parse("2026-09-01T09:00:00Z")

    @Test
    fun sportsUseTheirOwnCamelCaseWindowAndRejectIncompletePagination() {
        val request = xiaomiSportRequest(start, start.plusSeconds(86400), "next-page")
        assertEquals(setOf("startTime", "endTime", "reverse", "limit", "next_key"), request.keys)
        assertEquals(start.epochSecond.toString(), request.getValue("startTime").jsonPrimitive.content)
        assertEquals("50", request.getValue("limit").jsonPrimitive.content)
        assertEquals("next-page", xiaomiSportNextKey(Json.parseToJsonElement("""{"has_more":true,"next_key":"next-page"}""").jsonObject, null))
        assertNull(xiaomiSportNextKey(Json.parseToJsonElement("""{"has_more":false}""").jsonObject, null))
        for (invalid in listOf("""{"has_more":true}""", """{"has_more":true,"next_key":"same"}""", "{}")) {
            assertThrows(XiaomiCloudException.InvalidResponse::class.java) {
                xiaomiSportNextKey(Json.parseToJsonElement(invalid).jsonObject, "same")
            }
        }
    }

    private fun record(report: String, label: String = "sport_stats_swimming") = XiaomiParsers.records(
        XiaomiMetric.EXERCISE,
        listOf(XiaomiRawEntry("sport", start.epochSecond, report, label)),
        start.minusSeconds(1), start.plusSeconds(3600),
    ).single()

    @Test
    fun verifiedPoolReportPublishesOnlyNormalizedSummaryAndStrokeCodes() {
        val result = record("""{"sport_type":9,"start_time":${start.epochSecond},"duration":1800,"valid_duration":1500,"distance":1000,"calories":240,"pool_width":25,"turn_count":40,"main_posture":1,"avg_hrm":110,"min_hrm":80,"max_hrm":130,"route":[{"latitude":55}],"samples":[100,120],"avg_pace":15000}""")
        assertEquals("pool_swimming", result.values.getValue("exercise_type").jsonPrimitive.content)
        val details = result.values.getValue("swimming").jsonObject
        assertEquals("1000.0", details.getValue("distance_meters").jsonPrimitive.content)
        assertEquals("1500.0", details.getValue("active_duration_seconds").jsonPrimitive.content)
        assertEquals("25.0", details.getValue("pool_length_meters").jsonPrimitive.content)
        assertEquals("40", details.getValue("pool_lengths").jsonPrimitive.content)
        assertEquals("breaststroke", details.getValue("stroke_style").jsonPrimitive.content)
        assertEquals(setOf("distance_meters", "active_duration_seconds", "kilocalories", "pool_length_meters", "pool_lengths", "minimum_bpm", "average_bpm", "maximum_bpm", "stroke_style"), details.keys)
        assertFalse(result.toJson().toString().contains("route"))
        assertFalse(result.toJson().toString().contains("samples"))
    }

    @Test
    fun openWaterOtherSportsAndUnspecifiedSwimmingNeverBecomePool() {
        for (type in listOf(10, 1, 111)) {
            val result = record("""{"sport_type":$type,"duration":1800}""", "pool_swimming")
            assertFalse("swimming" in result.values)
            assertNotEquals("pool_swimming", result.values["exercise_type"]?.jsonPrimitive?.content)
        }
        assertFalse("swimming" in record("""{"duration":1800}""", "swimming").values)
    }

    @Test
    fun unknownAndMissingValuesStayAbsentWithoutPoisoningAnExercisePage() {
        val details = record("""{"sport_type":9,"duration":1800,"valid_duration":2000,"distance":-1,"main_posture":99,"pool_width":2500,"turn_count":1.5,"min_hrm":130,"max_hrm":100}""").values.getValue("swimming").jsonObject
        assertTrue(details.isEmpty())
        assertEquals("0.0", record("""{"sport_type":9,"duration":1800,"distance":0}""").values.getValue("swimming").jsonObject.getValue("distance_meters").jsonPrimitive.content)
    }

    @Test
    fun postureEnumIsExplicitAndUnknownIsNotInvented() {
        val expected = listOf("medley", "breaststroke", "freestyle", "backstroke", "butterfly")
        expected.forEachIndexed { code, name ->
            val report = Json.parseToJsonElement("""{"main_posture":$code}""").jsonObject
            assertEquals(name, XiaomiSwimming.details(report, 1800)["stroke_style"]?.jsonPrimitive?.content)
        }
        assertFalse("stroke_style" in XiaomiSwimming.details(Json.parseToJsonElement("""{"main_posture":-1}""").jsonObject, 1800))
    }
}
