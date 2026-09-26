package ru.tolstik.amigo.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class AppVersionTest {
    @Test
    fun releaseIdentityIsVersionOneFiveTwo() {
        assertEquals(19, BuildConfig.VERSION_CODE)
        assertEquals("1.5.2", BuildConfig.VERSION_NAME.removeSuffix("-debug"))
    }
}
