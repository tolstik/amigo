package ru.tolstik.amigo.sync.xiaomi

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import ru.tolstik.amigo.sync.wire.CanonicalJson

data class XiaomiCredentials(
    val userId: String,
    val cUserId: String,
    val passToken: String,
    val serviceToken: String,
    val ssecurity: String,
    val deviceId: String,
    val region: String,
) {
    val accountFingerprint: String
        get() = MessageDigest.getInstance("SHA-256")
            .digest(userId.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
}

class XiaomiCredentialStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true }

    @Synchronized
    fun save(credentials: XiaomiCredentials) {
        val existing = load()
        require(existing == null || existing.accountFingerprint == credentials.accountFingerprint) {
            "Сначала отключите текущий аккаунт Xiaomi"
        }
        preferences.edit().putString(KEY_CIPHERTEXT, seal(credentials)).commit()
    }

    /** Returns only AES-GCM ciphertext, safe to pass back from the isolated auth process. */
    fun seal(credentials: XiaomiCredentials): String {
        val plaintext = CanonicalJson.encode(buildJsonObject {
            put("c_user_id", credentials.cUserId)
            put("device_id", credentials.deviceId)
            put("pass_token", credentials.passToken)
            put("region", credentials.region)
            put("service_token", credentials.serviceToken)
            put("ssecurity", credentials.ssecurity)
            put("user_id", credentials.userId)
        })
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.doFinal(plaintext)
        val packed = ByteArray(1 + cipher.iv.size + encrypted.size)
        packed[0] = cipher.iv.size.toByte()
        cipher.iv.copyInto(packed, 1)
        encrypted.copyInto(packed, 1 + cipher.iv.size)
        plaintext.fill(0)
        return android.util.Base64.encodeToString(packed, android.util.Base64.NO_WRAP)
    }

    @Synchronized
    fun saveSealed(encoded: String) {
        val incoming = decrypt(encoded) ?: error("Не удалось расшифровать сессию Xiaomi")
        val existing = load()
        require(existing == null || existing.accountFingerprint == incoming.accountFingerprint) {
            "Сначала отключите текущий аккаунт Xiaomi"
        }
        preferences.edit().putString(KEY_CIPHERTEXT, encoded).commit()
    }

    @Synchronized
    fun load(): XiaomiCredentials? {
        val encoded = preferences.getString(KEY_CIPHERTEXT, null) ?: return null
        return decrypt(encoded)
    }

    private fun decrypt(encoded: String): XiaomiCredentials? = runCatching {
            val packed = android.util.Base64.decode(encoded, android.util.Base64.NO_WRAP)
            val ivSize = packed.first().toInt() and 0xff
            require(ivSize in 12..16 && packed.size > 1 + ivSize)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                key(),
                GCMParameterSpec(128, packed.copyOfRange(1, 1 + ivSize)),
            )
            val plaintext = cipher.doFinal(packed.copyOfRange(1 + ivSize, packed.size))
            try {
                val item = json.parseToJsonElement(plaintext.decodeToString()).jsonObject
                XiaomiCredentials(
                    userId = item.required("user_id"),
                    cUserId = item.optional("c_user_id"),
                    passToken = item.required("pass_token"),
                    serviceToken = item.required("service_token"),
                    ssecurity = item.required("ssecurity"),
                    deviceId = item.required("device_id"),
                    region = normalizeXiaomiRegion(item.required("region")),
                )
            } finally {
                plaintext.fill(0)
            }
        }.getOrNull()

    @Synchronized
    fun clear() {
        preferences.edit().remove(KEY_CIPHERTEXT).commit()
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (store.containsAlias(KEY_ALIAS)) store.deleteEntry(KEY_ALIAS)
    }

    fun hasCredentials(): Boolean = preferences.contains(KEY_CIPHERTEXT) && load() != null

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true)
                    .build(),
            )
            generateKey()
        }
    }

    companion object {
        private const val PREFERENCES = "amigo_xiaomi_secure"
        private const val KEY_CIPHERTEXT = "session_v1"
        private const val KEY_ALIAS = "amigo_xiaomi_session_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}

private fun kotlinx.serialization.json.JsonObject.required(key: String): String =
    getValue(key).jsonPrimitive.content.takeIf(String::isNotBlank)
        ?: error("Missing credential field")

private fun kotlinx.serialization.json.JsonObject.optional(key: String): String =
    get(key)?.jsonPrimitive?.content.orEmpty()

internal val XIAOMI_REGIONS = listOf("cn", "sg", "us", "de", "ru", "i2")

internal fun normalizeXiaomiRegion(raw: String?): String = when (raw?.trim()?.lowercase()) {
    "cn" -> "cn"
    "in", "i2" -> "i2"
    "ru" -> "ru"
    "us", "br", "ca", "mx" -> "us"
    "de", "gb", "fr", "it", "es", "nl", "pl", "se", "no", "dk", "fi", "at", "ch", "be" -> "de"
    else -> "sg"
}

internal fun xiaomiHealthHost(region: String): String =
    if (normalizeXiaomiRegion(region) == "cn") "hlth.io.mi.com"
    else "${normalizeXiaomiRegion(region)}.hlth.io.mi.com"
