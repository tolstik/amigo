package ru.tolstik.amigo.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class AppVersionTest {
    @Test
    fun releaseIdentityIsVersionOneFiveOne() {
        assertEquals(18, BuildConfig.VERSION_CODE)
        assertEquals("1.5.1", BuildConfig.VERSION_NAME.removeSuffix("-debug"))
    }
}
