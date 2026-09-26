package ru.tolstik.amigo.sync.xiaomi

import java.time.Instant
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class XiaomiStepAccumulatorTest {
    private val start = Instant.parse("2026-09-22T00:00:00Z")
    private val end = start.plusSeconds(86_400)

    private fun step(source: String, time: Long, count: Int) = XiaomiRawEntry(
        key = "steps",
        time = time,
        value = """{"time":$time,"steps":$count}""",
        sid = source,
    )

    @Test
    fun twoSourcesAndRepeatedPagesNeverDoubleTheSameHour() {
        val hour = start.epochSecond + 12 * 3600
        val first = listOf(step("watch", hour + 60, 1200), step("phone", hour + 60, 1190))
        val second = listOf(step("watch", hour + 120, 1351), step("phone", hour + 120, 1346))
        val accumulator = XiaomiStepAccumulator.empty()
        accumulator.add(first, start, end)
        val savedCursor = accumulator.encode()
        assertFalse(savedCursor.contains("watch"))
        assertFalse(savedCursor.contains("phone"))
        val resumed = XiaomiStepAccumulator.decode(savedCursor)
        resumed.add(first + second + second, start, end)
        val result = resumed.records().single()
        assertEquals("2551", result.values.getValue("count").jsonPrimitive.content)
        assertEquals(result, XiaomiStepAccumulator.decode(resumed.encode()).records().single())
    }

    @Test
    fun differentSourcesAreNotSummedEvenWhenTheirBinsAreOffset() {
        val hour = start.epochSecond + 12 * 3600
        val samples = listOf(
            step("watch", hour + 60, 1200), step("watch", hour + 120, 1351),
            step("phone", hour + 90, 1190), step("phone", hour + 150, 1346),
        )
        val accumulator = XiaomiStepAccumulator.empty()
        accumulator.add(samples, start, end)
        assertEquals("2551", accumulator.records().single().values.getValue("count").jsonPrimitive.content)
    }

    @Test
    fun newSnapshotsRejectRecordsWithoutSourceRatherThanPublishAnUnknownSum() {
        val hour = start.epochSecond + 12 * 3600
        val accumulator = XiaomiStepAccumulator.empty()
        try {
            accumulator.add(listOf(step("", hour + 60, 100)), start, end)
            throw AssertionError("Missing Xiaomi sid was accepted")
        } catch (expected: IllegalStateException) {
            assertTrue(accumulator.records().isEmpty())
        }
    }

    @Test
    fun separateHoursAndSameSourceCountsArePreserved() {
        val hour = start.epochSecond + 12 * 3600
        val accumulator = XiaomiStepAccumulator.empty()
        accumulator.add(
            listOf(
                step("watch", hour + 60, 1200), step("watch", hour + 120, 1351),
                step("watch", hour + 3660, 500), step("phone", hour + 3660, 450),
                step("watch", end.epochSecond, 999),
            ),
            start, end,
        )
        val values = accumulator.records().map { it.values.getValue("count").jsonPrimitive.content }
        assertEquals(listOf("2551", "500"), values)
        assertTrue(XiaomiStepAccumulator.decode("").records().isEmpty())
    }
}
