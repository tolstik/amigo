package ru.tolstik.amigo.sync.network

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class BatchRequestThrottleTest {
    @Test
    fun spacesBatchRequestsBelowSixtyPerMinuteWithoutDelayingFirstRequest() = runTest {
        var nowNanos = 0L
        val sleeps = mutableListOf<Long>()
        val permits = mutableListOf<Long>()
        val throttle = BatchRequestThrottle(
            minimumIntervalMillis = 1_100,
            nanoTime = { nowNanos },
            sleepMillis = { millis ->
                sleeps += millis
                nowNanos += millis * 1_000_000
            },
        )

        throttle.awaitPermit()
        permits += nowNanos
        nowNanos += 100 * 1_000_000
        throttle.awaitPermit()
        permits += nowNanos
        nowNanos += 1_100 * 1_000_000
        throttle.awaitPermit()
        permits += nowNanos

        assertEquals(listOf(1_000L), sleeps)
        assertEquals(listOf(0L, 1_100_000_000L, 2_200_000_000L), permits)
    }
}
