package ru.tolstik.amigo.sync.xiaomi

import java.io.IOException
import java.time.Instant
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import ru.tolstik.amigo.sync.wire.CanonicalJson

internal sealed class XiaomiCloudException(message: String, cause: Throwable? = null) : IOException(message, cause) {
    class AuthRequired(cause: Throwable? = null) : XiaomiCloudException("Xiaomi session expired", cause)
    class RateLimited : XiaomiCloudException("Xiaomi Cloud rate limited the request")
    class Network(cause: Throwable) : XiaomiCloudException("Xiaomi Cloud network error", cause)
    class InvalidResponse(cause: Throwable? = null) : XiaomiCloudException("Xiaomi Cloud returned invalid data", cause)
}

internal data class XiaomiCloudPage(
    val entries: List<XiaomiRawEntry>,
    val nextKey: String?,
    val sourceDataAsOf: Instant?,
)

internal data class XiaomiRawEntry(
    val key: String,
    val time: Long,
    val value: String,
    val category: String? = null,
)

internal class XiaomiCloudClient(
    private val credentials: XiaomiCredentials,
    http: OkHttpClient,
    private val crypto: XiaomiCloudCrypto = XiaomiCloudCrypto(),
) {
    private val http = http.newBuilder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .callTimeout(30, TimeUnit.SECONDS)
        .followRedirects(false)
        .build()
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun dataPage(
        cloudKey: String,
        from: Instant,
        until: Instant,
        nextKey: String?,
        region: String = credentials.region,
    ): XiaomiCloudPage {
        val payload = buildJsonObject {
            put("end_time", until.epochSecond)
            put("key", cloudKey)
            nextKey?.takeIf(String::isNotBlank)?.let { put("next_key", it) }
            put("reverse", true)
            put("start_time", from.epochSecond)
        }
        return parseFitnessPage(
            encryptedPost(
                host = xiaomiHealthHost(region),
                path = "/app/v1/data/get_fitness_data_by_time",
                payload = payload,
            ),
        )
    }

    suspend fun latestProbe(region: String): XiaomiCloudPage {
        val payload = buildJsonObject {
            put("params", buildJsonArray {
                listOf("heart_rate", "steps", "sleep").forEach { key ->
                    add(buildJsonObject {
                        put("key", key)
                        put("limit", 10)
                    })
                }
            })
        }
        return parseFitnessPage(
            encryptedPost(
                host = xiaomiHealthHost(region),
                path = "/app/v1/data/get_latest_fitness_data",
                payload = payload,
            ),
        )
    }

    suspend fun latestData(cloudKey: String, limit: Int = 30): XiaomiCloudPage {
        val payload = buildJsonObject {
            put("params", buildJsonArray {
                add(buildJsonObject {
                    put("key", cloudKey)
                    put("limit", limit.coerceIn(1, 100))
                })
            })
        }
        return parseFitnessPage(
            encryptedPost(
                host = xiaomiHealthHost(credentials.region),
                path = "/app/v1/data/get_latest_fitness_data",
                payload = payload,
            ),
        )
    }

    suspend fun sportPage(
        from: Instant,
        until: Instant,
        nextKey: String?,
    ): XiaomiCloudPage {
        val payload = buildJsonObject {
            put("end_time", until.epochSecond)
            put("limit", 50)
            nextKey?.takeIf(String::isNotBlank)?.let { put("next_key", it) }
            put("start_time", from.epochSecond)
        }
        val response = encryptedPost(
            host = xiaomiHealthHost(credentials.region),
            path = "/app/v1/data/get_sport_records_by_time",
            payload = payload,
        )
        val result = response.jsonObject["result"]?.jsonObject ?: JsonObject(emptyMap())
        val elements = result["sport_records"]?.jsonArray.orEmpty()
        return XiaomiCloudPage(
            entries = elements.mapNotNull(::rawEntry),
            nextKey = result["next_key"]?.asString()?.takeIf(String::isNotBlank)
                ?.takeIf { result["has_more"]?.jsonPrimitive?.content == "true" },
            sourceDataAsOf = maxProviderTime(elements),
        )
    }

    private suspend fun encryptedPost(
        host: String,
        path: String,
        payload: JsonObject,
    ): JsonElement = withContext(Dispatchers.IO) {
        require(host == "hlth.io.mi.com" || XIAOMI_REGIONS.drop(1).any { host == "$it.hlth.io.mi.com" })
        val plaintext = CanonicalJson.render(payload)
        val encrypted = crypto.build(path, credentials.ssecurity, plaintext)
        val request = Request.Builder()
            .url("https://$host$path")
            .header("User-Agent", USER_AGENT)
            .header("Cookie", cookieHeader())
            .post(
                FormBody.Builder()
                    .add("_nonce", encrypted.nonce)
                    .add("data", encrypted.data)
                    .add("rc4_hash__", encrypted.rc4Hash)
                    .add("signature", encrypted.signature)
                    .build(),
            )
            .build()
        val responseText = try {
            http.newCall(request).execute().use { response ->
                when (response.code) {
                    401, 403 -> throw XiaomiCloudException.AuthRequired()
                    429 -> throw XiaomiCloudException.RateLimited()
                }
                if (!response.isSuccessful) throw XiaomiCloudException.InvalidResponse()
                response.body?.string()?.trim() ?: throw XiaomiCloudException.InvalidResponse()
            }
        } catch (error: XiaomiCloudException) {
            throw error
        } catch (error: IOException) {
            throw XiaomiCloudException.Network(error)
        }
        val decrypted = try {
            crypto.decrypt(encrypted.signedNonce, responseText).decodeToString()
        } catch (error: Exception) {
            throw XiaomiCloudException.AuthRequired(error)
        }
        val result = try {
            json.parseToJsonElement(decrypted)
        } catch (error: Exception) {
            throw XiaomiCloudException.InvalidResponse(error)
        }
        val code = result.jsonObject["code"]?.jsonPrimitive?.content?.toIntOrNull()
        val message = result.jsonObject["message"]?.asString()
            ?: result.jsonObject["msg"]?.asString()
        if (code != null && code != 0) {
            if (code in AUTH_CODES || message.orEmpty().contains("auth", ignoreCase = true) ||
                message.orEmpty().contains("token", ignoreCase = true)
            ) {
                throw XiaomiCloudException.AuthRequired()
            }
            if (code == 429 || message.orEmpty().contains("rate", ignoreCase = true)) {
                throw XiaomiCloudException.RateLimited()
            }
            throw XiaomiCloudException.InvalidResponse()
        }
        result
    }

    private fun parseFitnessPage(response: JsonElement): XiaomiCloudPage {
        val result = response.jsonObject["result"]?.jsonObject ?: JsonObject(emptyMap())
        val rawList = result["data_list"]?.jsonArray.orEmpty()
        val flattened = rawList.flatMap { element ->
            val objectValue = runCatching { element.jsonObject }.getOrNull()
            val nested = objectValue?.get("data_list") as? JsonArray
            nested?.toList() ?: listOf(element)
        }
        return XiaomiCloudPage(
            entries = flattened.mapNotNull(::rawEntry),
            nextKey = result["next_key"]?.asString()?.takeIf(String::isNotBlank)
                ?.takeIf { result["has_more"]?.jsonPrimitive?.content == "true" },
            sourceDataAsOf = maxProviderTime(flattened),
        )
    }

    private fun rawEntry(element: JsonElement): XiaomiRawEntry? {
        val item = runCatching { element.jsonObject }.getOrNull() ?: return null
        val valueElement = item["value"] ?: return null
        val value = if ((valueElement as? JsonPrimitive)?.isString == true) {
            valueElement.content
        } else {
            valueElement.toString()
        }
        return XiaomiRawEntry(
            key = item["key"]?.asString().orEmpty(),
            time = item["time"]?.jsonPrimitive?.longOrNull ?: 0,
            value = value,
            category = item["category"]?.asString(),
        )
    }

    private fun maxProviderTime(elements: List<JsonElement>): Instant? {
        val upper = Instant.now().plusSeconds(86_400).epochSecond
        fun visit(element: JsonElement, key: String? = null): Long? = when (element) {
            is JsonArray -> element.mapNotNull { visit(it) }.maxOrNull()
            is JsonObject -> element.entries.mapNotNull { (childKey, child) ->
                visit(child, childKey)
            }.maxOrNull()
            is JsonPrimitive -> if (
                key in PROVIDER_TIME_KEYS
            ) {
                element.longOrNull?.takeIf { it in 946_684_800..upper }
            } else null
        }
        val latest = elements.mapNotNull { visit(it) }.maxOrNull() ?: return null
        return Instant.ofEpochSecond(latest)
    }

    private fun cookieHeader(): String = buildList {
        add("serviceToken=${credentials.serviceToken}")
        add(
            if (credentials.cUserId.isNotBlank()) "cUserId=${credentials.cUserId}"
            else "userId=${credentials.userId}",
        )
        add("locale=en")
        add("auth_key=$AUTH_KEY")
    }.joinToString("; ")

    companion object {
        internal const val USER_AGENT = "APP/com.xiaomi.miwatch.pro APPV/3.49.1 " +
            "iosPassportSDK/4.2.64 iOS/18.7.8 MK/aVBob25lMTQsMw== " +
            "DEVT/aVBob25l DEVS/aU9T BRA/QXBwbGU= L/en_US miHSTS"
        private const val AUTH_KEY = "rwelJuWBFJxmbMKD"
        private val AUTH_CODES = setOf(3, 401, 403, 70016, 10016)
        private val PROVIDER_TIME_KEYS = setOf(
            "time", "start_time", "end_time", "date_time", "bedtime", "wake_up_time",
            "bed_timestamp", "out_bed_timestamp", "hrv_analysis_timestamp",
        )
    }
}

private fun JsonElement.asString(): String? =
    (this as? JsonPrimitive)?.contentOrNull
