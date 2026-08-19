package ru.tolstik.amigo.sync.network

import org.junit.Assert.assertEquals
import org.junit.Test

class IngestApiTest {
    @Test
    fun validatedServerErrorCodeIsIncludedInTheUserVisibleMessage() {
        val body = """{"detail":{"code":"invalid_step_count"}}"""

        assertEquals(
            "Amigo server returned HTTP 422 (invalid_step_count)",
            serverErrorMessage(422, body),
        )
    }

    @Test
    fun malformedOrUnsafeServerBodyIsNotReflected() {
        assertEquals(
            "Amigo server returned HTTP 422",
            serverErrorMessage(422, """{"detail":{"code":"<server output>"}}"""),
        )
        assertEquals(
            "Amigo server returned HTTP 500",
            serverErrorMessage(500, "not-json"),
        )
        assertEquals(
            "Amigo server returned HTTP 422",
            serverErrorMessage(422, """{"detail":{"code":123456}}"""),
        )
        assertEquals(
            "Amigo server returned HTTP 422",
            serverErrorMessage(422, """{"detail":{"code":true}}"""),
        )
    }
}
