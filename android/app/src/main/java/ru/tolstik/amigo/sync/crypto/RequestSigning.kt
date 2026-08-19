package ru.tolstik.amigo.sync.crypto

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.nio.charset.StandardCharsets
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Base64

interface RequestSigner {
    fun publicKeyPem(): String

    fun sign(payload: ByteArray): ByteArray

    fun rotate()
}

object SignatureInput {
    fun create(timestamp: Long, nonce: String, batchId: String, body: ByteArray): ByteArray {
        require(timestamp > 0)
        require(nonce.isNotBlank())
        require(batchId.isNotBlank())
        val prefix = "$timestamp\n$nonce\n$batchId\n".toByteArray(StandardCharsets.UTF_8)
        return prefix + body
    }
}

object P256Signatures {
    fun sign(privateKey: PrivateKey, payload: ByteArray): ByteArray =
        Signature.getInstance("SHA256withECDSA").run {
            initSign(privateKey)
            update(payload)
            sign()
        }
}

class AndroidKeyStoreSigner(
    private val alias: String = "amigo-health-sync-device-v1",
) : RequestSigner {
    private val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun ensureKey() {
        if (keyStore.containsAlias(alias)) return
        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").run {
            initialize(
                KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .setUserAuthenticationRequired(false)
                    .build(),
            )
            generateKeyPair()
        }
    }

    @Synchronized
    override fun publicKeyPem(): String {
        ensureKey()
        val encoded = Base64.getMimeEncoder(64, "\n".toByteArray()).encodeToString(
            keyStore.getCertificate(alias).publicKey.encoded,
        )
        return "-----BEGIN PUBLIC KEY-----\n$encoded\n-----END PUBLIC KEY-----"
    }

    @Synchronized
    override fun sign(payload: ByteArray): ByteArray {
        ensureKey()
        val key = keyStore.getKey(alias, null) as PrivateKey
        return P256Signatures.sign(key, payload)
    }

    @Synchronized
    override fun rotate() {
        if (keyStore.containsAlias(alias)) keyStore.deleteEntry(alias)
        ensureKey()
    }
}

class PairingResetter(
    private val signer: RequestSigner,
    private val clearLocalPairing: () -> Unit,
) {
    fun reset() {
        signer.rotate()
        clearLocalPairing()
    }
}

class NonceGenerator(private val random: SecureRandom = SecureRandom()) {
    fun next(): String = ByteArray(18).also(random::nextBytes).let {
        Base64.getUrlEncoder().withoutPadding().encodeToString(it)
    }
}
