package ru.tolstik.amigo.sync.xiaomi

import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Test

class XiaomiSyncCoordinatorTest {
    @Test
    fun safeServerRejectionRemainsVisibleInsteadOfMasqueradingAsCloudFailure() {
        assertEquals(
            "mi_fitness_not_enabled",
            xiaomiSyncErrorCode(
                IOException("Amigo server returned HTTP 409 (mi_fitness_not_enabled)"),
            ),
        )
    }

    @Test
    fun unsafeOrProviderErrorsRemainGeneric() {
        assertEquals(
            "invalid_cloud_response",
            xiaomiSyncErrorCode(IOException("provider body: private-value")),
        )
        assertEquals(
            "network_error",
            xiaomiSyncErrorCode(XiaomiCloudException.Network(IOException("offline"))),
        )
    }
}
