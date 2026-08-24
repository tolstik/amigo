package ru.tolstik.amigo.sync.xiaomi

import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Clock
import java.util.Base64

internal data class XiaomiEncryptedRequest(
    val nonce: String,
    val data: String,
    val rc4Hash: String,
    val signature: String,
    val signedNonce: String,
)

/**
 * Xiaomi's SHA/RC4 request envelope, derived from Better Mi Fitness Sync at
 * commit 06010b39d72e30d8197a7428b76638ecd1637e03 (MIT).
 */
internal class XiaomiCloudCrypto(
    private val clock: Clock = Clock.systemUTC(),
    private val random: SecureRandom = SecureRandom(),
) {
    fun build(
        path: String,
        ssecurity: String,
        plaintext: String,
        fixedNonce: String? = null,
    ): XiaomiEncryptedRequest {
        require(path.startsWith("/"))
        val nonce = fixedNonce ?: generateNonce()
        val signedNonce = base64(
            digest("SHA-256", decode(ssecurity) + decode(nonce)),
        )
        val preHash = "POST&$path&data=$plaintext&$signedNonce"
        val rc4HashPlain = base64(digest("SHA-1", preHash.toByteArray(Charsets.UTF_8)))
        val rc4Hash = base64(rc4(signedNonce, rc4HashPlain.toByteArray(Charsets.UTF_8)))
        val encryptedData = base64(rc4(signedNonce, plaintext.toByteArray(Charsets.UTF_8)))
        val signatureInput =
            "POST&$path&data=$encryptedData&rc4_hash__=$rc4Hash&$signedNonce"
        return XiaomiEncryptedRequest(
            nonce = nonce,
            data = encryptedData,
            rc4Hash = rc4Hash,
            signature = base64(digest("SHA-1", signatureInput.toByteArray(Charsets.UTF_8))),
            signedNonce = signedNonce,
        )
    }

    fun decrypt(signedNonce: String, response: String): ByteArray =
        rc4(signedNonce, decode(response.trim()))

    private fun generateNonce(): String {
        val buffer = ByteArray(12)
        random.nextBytes(buffer)
        ByteBuffer.wrap(buffer, 8, 4).putInt((clock.instant().epochSecond / 60).toInt())
        return base64(buffer)
    }

    private fun rc4(signedNonce: String, input: ByteArray): ByteArray =
        SkippedRc4(decode(signedNonce)).process(input)

    private fun digest(name: String, bytes: ByteArray): ByteArray =
        MessageDigest.getInstance(name).digest(bytes)

    private fun base64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)
    private fun decode(value: String): ByteArray = Base64.getDecoder().decode(value)
}

internal class SkippedRc4(key: ByteArray) {
    private val state = IntArray(256) { it }
    private var i = 0
    private var j = 0

    init {
        require(key.isNotEmpty())
        var swap = 0
        for (index in state.indices) {
            swap = (swap + state[index] + (key[index % key.size].toInt() and 0xff)) and 0xff
            val temporary = state[index]
            state[index] = state[swap]
            state[swap] = temporary
        }
        repeat(1024) { nextByte() }
    }

    fun process(input: ByteArray): ByteArray = ByteArray(input.size) { index ->
        (input[index].toInt() xor nextByte()).toByte()
    }

    private fun nextByte(): Int {
        i = (i + 1) and 0xff
        j = (j + state[i]) and 0xff
        val temporary = state[i]
        state[i] = state[j]
        state[j] = temporary
        return state[(state[i] + state[j]) and 0xff]
    }
}
