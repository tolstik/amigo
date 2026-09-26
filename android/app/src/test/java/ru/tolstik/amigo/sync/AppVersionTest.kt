package ru.tolstik.amigo.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class AppVersionTest {
    @Test
    fun releaseIdentityIsVersionOneFiveFour() {
        assertEquals(21, BuildConfig.VERSION_CODE)
        assertEquals("1.5.4", BuildConfig.VERSION_NAME.removeSuffix("-debug"))
    }
}
