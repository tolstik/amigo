package ru.tolstik.amigo.sync.network

import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

internal const val MIN_BATCH_REQUEST_INTERVAL_MILLIS = 1_100L

/** Keeps batch traffic below nginx's 60 requests/minute limit, including timing jitter. */
class BatchRequestThrottle(
    private val minimumIntervalMillis: Long = MIN_BATCH_REQUEST_INTERVAL_MILLIS,
    private val nanoTime: () -> Long = System::nanoTime,
    private val sleepMillis: suspend (Long) -> Unit = { delay(it) },
) {
    private val mutex = Mutex()
    private var lastPermitNanos: Long? = null

    init {
        require(minimumIntervalMillis > 0)
    }

    suspend fun awaitPermit() = mutex.withLock {
        val intervalNanos = minimumIntervalMillis * NANOS_PER_MILLISECOND
        lastPermitNanos?.let { previous ->
            val remainingNanos = intervalNanos - (nanoTime() - previous)
            if (remainingNanos > 0) {
                sleepMillis(
                    (remainingNanos + NANOS_PER_MILLISECOND - 1) / NANOS_PER_MILLISECOND,
                )
            }
        }
        lastPermitNanos = nanoTime()
    }

    private companion object {
        const val NANOS_PER_MILLISECOND = 1_000_000L
    }
}
