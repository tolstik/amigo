package ru.tolstik.amigo.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class AppVersionTest {
    @Test
    fun releaseIdentityIsVersionOneFiveThree() {
        assertEquals(20, BuildConfig.VERSION_CODE)
        assertEquals("1.5.3", BuildConfig.VERSION_NAME.removeSuffix("-debug"))
    }
}
