package ru.tolstik.amigo.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class AppVersionTest {
    @Test
    fun releaseIdentityIsVersionOneFourOne() {
        assertEquals(16, BuildConfig.VERSION_CODE)
        assertEquals("1.4.1", BuildConfig.VERSION_NAME.removeSuffix("-debug"))
    }
}
