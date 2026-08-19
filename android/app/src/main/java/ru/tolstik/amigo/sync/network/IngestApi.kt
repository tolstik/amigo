package ru.tolstik.amigo.sync.network

import java.io.IOException
import java.time.Clock
import java.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import ru.tolstik.amigo.sync.crypto.NonceGenerator
import ru.tolstik.amigo.sync.crypto.RequestSigner
import ru.tolstik.amigo.sync.crypto.SignatureInput
import ru.tolstik.amigo.sync.data.AppPreferences
import ru.tolstik.amigo.sync.sync.BatchEnvelope
import ru.tolstik.amigo.sync.sync.BatchUploader
import ru.tolstik.amigo.sync.sync.INGEST_BODY_LIMIT_BYTES
import ru.tolstik.amigo.sync.wire.CanonicalJson

data class RegistrationResponse(
    val deviceId: String,
    val pairingCode: String?,
    val status: String,
)

data class DeviceStatusResponse(
    val deviceId: String,
    val status: String,
    val pairingCode: String?,
    val lastSyncAt: String?,
    val dataAsOf: String?,
    val lastError: String?,
)

class IngestApi(
    private val http: OkHttpClient,
    private val signer: RequestSigner,
    private val preferences: AppPreferences,
    private val clock: Clock = Clock.systemUTC(),
    private val nonceGenerator: NonceGenerator = NonceGenerator(),
    private val batchRequestThrottle: BatchRequestThrottle = BatchRequestThrottle(),
) : BatchUploader {
    private val json = Json { ignoreUnknownKeys = true }
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    suspend fun register(serverUrl: String, label: String): RegistrationResponse {
        val endpoint = endpoint(serverUrl, "/amigo-ingest/v1/devices/register")
        val body = CanonicalJson.encode(buildJsonObject {
            put("label", label.trim().ifBlank { "Android" })
            put("public_key_pem", signer.publicKeyPem())
        })
        val request = Request.Builder()
            .url(endpoint)
            .post(body.toRequestBody(jsonMediaType))
            .build()
        val payload = executeJson(request)
        val status = payload.requiredString("status")
        val pairingCode = payload.optionalString("pairing_code")
        if (status == "pending" && pairingCode.isNullOrBlank()) {
            throw IOException("Amigo server response misses pairing_code")
        }
        return RegistrationResponse(
            deviceId = payload.requiredString("device_id"),
            pairingCode = pairingCode,
            status = status,
        )
    }

    suspend fun status(serverUrl: String, deviceId: String): DeviceStatusResponse {
        val request = Request.Builder()
            .url(endpoint(serverUrl, "/amigo-ingest/v1/devices/$deviceId/status"))
            .get()
            .build()
        val payload = executeJson(request)
        return DeviceStatusResponse(
            deviceId = payload.requiredString("device_id"),
            status = payload.requiredString("status"),
            pairingCode = payload.optionalString("pairing_code"),
            lastSyncAt = payload.optionalString("last_sync_at"),
            dataAsOf = payload.optionalString("data_as_of"),
            lastError = payload.optionalString("last_error"),
        )
    }

    override suspend fun upload(batch: BatchEnvelope) {
        val registration = preferences.registration()
            ?: throw IllegalStateException("Device is not registered")
        check(registration.status == "approved") { "Device pairing is not approved" }
        val body = CanonicalJson.encode(batch.toJson())
        require(body.size < INGEST_BODY_LIMIT_BYTES) { "Ingest batch body is too large" }
        batchRequestThrottle.awaitPermit()
        val timestamp = clock.instant().epochSecond
        val nonce = nonceGenerator.next()
        val input = SignatureInput.create(timestamp, nonce, batch.batchId, body)
        val signature = Base64.getEncoder().encodeToString(signer.sign(input))
        val request = Request.Builder()
            .url(endpoint(registration.serverUrl, "/amigo-ingest/v1/health-connect/batches"))
            .header("X-Amigo-Device-Id", registration.deviceId)
            .header("X-Amigo-Timestamp", timestamp.toString())
            .header("X-Amigo-Nonce", nonce)
            .header("X-Amigo-Batch-Id", batch.batchId)
            .header("X-Amigo-Signature", signature)
            .post(body.toRequestBody(jsonMediaType))
            .build()
        executeNoContent(request)
    }

    private suspend fun executeJson(request: Request): JsonObject = withContext(Dispatchers.IO) {
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("Amigo server returned HTTP ${response.code}")
            val body = response.body?.string() ?: throw IOException("Amigo server returned no body")
            try {
                json.parseToJsonElement(body).jsonObject
            } catch (error: Exception) {
                throw IOException("Amigo server returned invalid JSON", error)
            }
        }
    }

    private suspend fun executeNoContent(request: Request) = withContext(Dispatchers.IO) {
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("Amigo server returned HTTP ${response.code}")
        }
    }

    private fun endpoint(serverUrl: String, path: String): String {
        val parsed = serverUrl.trim().trimEnd('/').toHttpUrlOrNull()
            ?: throw IllegalArgumentException("Invalid server URL")
        require(parsed.scheme == "https") { "Only HTTPS server URLs are allowed" }
        require(parsed.encodedPath == "/" && parsed.query == null && parsed.fragment == null) {
            "Server URL must contain only scheme and host"
        }
        return parsed.newBuilder().encodedPath(path).build().toString()
    }
}

private fun JsonObject.requiredString(key: String): String =
    optionalString(key)?.takeIf(String::isNotBlank)
        ?: throw IOException("Amigo server response misses $key")

private fun JsonObject.optionalString(key: String): String? =
    (this[key] as? JsonPrimitive)?.takeUnless { it.isString.not() && it.content == "null" }?.content
