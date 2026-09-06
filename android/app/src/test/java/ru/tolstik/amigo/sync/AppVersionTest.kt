package ru.tolstik.amigo.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class AppVersionTest {
    @Test
    fun releaseIdentityIsVersionOneFiveZero() {
        assertEquals(17, BuildConfig.VERSION_CODE)
        assertEquals("1.5.0", BuildConfig.VERSION_NAME.removeSuffix("-debug"))
    }
}
