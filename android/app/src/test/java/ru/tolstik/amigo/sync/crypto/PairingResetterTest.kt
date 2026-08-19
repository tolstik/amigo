package ru.tolstik.amigo.sync.crypto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingResetterTest {
    @Test
    fun rotatesSigningKeyBeforeClearingLocalPairing() {
        val events = mutableListOf<String>()
        val signer = FakeSigner(onRotate = { events += "rotate" })

        PairingResetter(signer) { events += "clear" }.reset()

        assertEquals(listOf("rotate", "clear"), events)
    }

    @Test
    fun keepsPairingStateWhenKeyRotationFails() {
        var cleared = false
        val signer = FakeSigner(onRotate = { error("keystore unavailable") })

        val failure = runCatching {
            PairingResetter(signer) { cleared = true }.reset()
        }

        assertTrue(failure.isFailure)
        assertTrue(!cleared)
    }

    private class FakeSigner(
        private val onRotate: () -> Unit,
    ) : RequestSigner {
        override fun publicKeyPem() = "unused"

        override fun sign(payload: ByteArray) = error("unused")

        override fun rotate() = onRotate()
    }
}
