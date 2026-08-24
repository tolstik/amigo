package ru.tolstik.amigo.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class AppVersionTest {
    @Test
    fun releaseIdentityIsVersionOneFourZero() {
        assertEquals(15, BuildConfig.VERSION_CODE)
        assertEquals("1.4.0", BuildConfig.VERSION_NAME.removeSuffix("-debug"))
    }
}
