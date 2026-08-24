package ru.tolstik.amigo.sync.xiaomi

import java.io.IOException
import java.net.URI
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

internal class XiaomiPassportClient(baseHttp: OkHttpClient) {
    private val json = Json { ignoreUnknownKeys = true }
    private val baseHttp = baseHttp.newBuilder()
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    suspend fun completeBrowserLogin(
        callbackUrl: String,
        accountCookies: String?,
        stsCookies: String?,
    ): XiaomiCredentials = withContext(Dispatchers.IO) {
        val parsed = callbackUrl.toHttpUrl()
        require(parsed.isHttps && parsed.host == STS_HOST && parsed.encodedPath.startsWith("/healthapp/")) {
            "Недопустимый Xiaomi callback"
        }
        val jar = MemoryCookieJar()
        jar.seed(ACCOUNT_URL, accountCookies)
        jar.seed(STS_URL, stsCookies)
        followRedirects(client(jar), callbackUrl)
        val userId = jar.value("userId") ?: error("Xiaomi не вернул userId")
        val passToken = jar.value("passToken") ?: error("Xiaomi не вернул обновляемую сессию")
        val deviceId = parsed.queryParameter("d")
            ?: parsed.queryParameter("deviceId")
            ?: jar.value("deviceId")
            ?: newDeviceId()
        refreshFromCookies(
            jar = jar,
            userId = userId,
            passToken = passToken,
            deviceId = deviceId,
            previousRegion = parsed.queryParameter("p_ur"),
            previousCUserId = jar.value("cUserId").orEmpty(),
        )
    }

    suspend fun refresh(credentials: XiaomiCredentials): XiaomiCredentials =
        withContext(Dispatchers.IO) {
            val jar = MemoryCookieJar().apply {
                seed(ACCOUNT_URL, "userId=${credentials.userId}; passToken=${credentials.passToken}; deviceId=${credentials.deviceId}")
            }
            refreshFromCookies(
                jar = jar,
                userId = credentials.userId,
                passToken = credentials.passToken,
                deviceId = credentials.deviceId,
                previousRegion = credentials.region,
                previousCUserId = credentials.cUserId,
            )
        }

    private fun refreshFromCookies(
        jar: MemoryCookieJar,
        userId: String,
        passToken: String,
        deviceId: String,
        previousRegion: String?,
        previousCUserId: String,
    ): XiaomiCredentials {
        val http = client(jar)
        val loginUrl = ACCOUNT_URL.newBuilder()
            .addPathSegments("pass/serviceLogin")
            .addQueryParameter("sid", SID)
            .addQueryParameter("_json", "true")
            .addQueryParameter("callback", STS_CALLBACK)
            .build()
        val response = execute(http, loginUrl.toString())
        val rePassToken = response.header("re-pass-token")
            ?: response.header("Re-Pass-Token")
        val responseObject = response.use {
            val body = it.body?.string()?.removePrefix("&&&START&&&")?.trim().orEmpty()
            runCatching { json.parseToJsonElement(body).jsonObject }
                .getOrElse { throw IOException("Xiaomi Passport вернул неверный ответ") }
        }
        val code = responseObject["code"]?.jsonPrimitive?.content?.toIntOrNull() ?: -1
        val securityStatus = responseObject["securityStatus"]
            ?.jsonPrimitive?.content?.toIntOrNull() ?: 0
        if (code != 0 || securityStatus != 0) {
            throw XiaomiCloudException.AuthRequired()
        }
        val ssecurity = responseObject.string("ssecurity")
            .ifBlank { harvestSsecurity(http, userId, passToken, deviceId) }
        if (ssecurity.isBlank()) throw XiaomiCloudException.AuthRequired()
        val location = responseObject.string("location")
        val nonce = responseObject["nonce"]?.jsonPrimitive?.longOrNull?.toString()
            ?: responseObject.string("nonce")
        var region = previousRegion.orEmpty()
        if (location.isNotBlank()) {
            val signedLocation = if (nonce.isNotBlank()) {
                val separator = if (location.contains('?')) '&' else '?'
                val clientSign = Base64.getEncoder().encodeToString(
                    MessageDigest.getInstance("SHA-1")
                        .digest("nonce=$nonce&$ssecurity".toByteArray(Charsets.UTF_8)),
                )
                "$location${separator}clientSign=${java.net.URLEncoder.encode(clientSign, "UTF-8")}" +
                    "&_userIdNeedEncrypt=true"
            } else {
                location
            }
            region = followRedirects(http, absoluteAccountUrl(signedLocation)).ifBlank { region }
        }
        val serviceToken = jar.value("${SID}_serviceToken") ?: jar.value("serviceToken")
            ?: throw XiaomiCloudException.AuthRequired()
        val effectivePassToken = preferRotatedPassToken(
            oldPassToken = passToken,
            newPassToken = responseObject.string("passToken"),
            rePassTokenHeader = rePassToken,
        )
        val effectiveUserId = responseObject.string("userId").ifBlank { userId }
        val cUserId = responseObject.string("cUserId")
            .ifBlank { responseObject.string("encryptedUserId") }
            .ifBlank { jar.value("cUserId").orEmpty() }
            .ifBlank { previousCUserId }
        return XiaomiCredentials(
            userId = effectiveUserId,
            cUserId = cUserId,
            passToken = effectivePassToken,
            serviceToken = serviceToken,
            ssecurity = ssecurity,
            deviceId = deviceId,
            region = normalizeXiaomiRegion(region),
        )
    }

    private fun harvestSsecurity(
        http: OkHttpClient,
        userId: String,
        passToken: String,
        deviceId: String,
    ): String {
        val url = ACCOUNT_URL.newBuilder()
            .addPathSegments("pass/serviceLogin")
            .addQueryParameter("sid", SID)
            .addQueryParameter("_json", "true")
            .build()
        return execute(
            http,
            url.toString(),
            cookieOverride = "userId=$userId; passToken=$passToken; deviceId=$deviceId",
        ).use { response ->
            val pragma = response.header("Extension-Pragma")
            val pragmaValue = pragma?.let {
                runCatching { json.parseToJsonElement(it).jsonObject.string("ssecurity") }.getOrNull()
            }
            pragmaValue.orEmpty().ifBlank {
                val body = response.body?.string()?.removePrefix("&&&START&&&")?.trim().orEmpty()
                runCatching { json.parseToJsonElement(body).jsonObject.string("ssecurity") }
                    .getOrDefault("")
            }
        }
    }

    private fun followRedirects(http: OkHttpClient, initialUrl: String): String {
        var current = initialUrl
        var region = ""
        repeat(12) {
            val url = current.toHttpUrl()
            requireAllowedPassportHost(url)
            url.queryParameter("p_ur")?.let { region = it }
            execute(http, current).use { response ->
                val location = response.header("Location")
                if (response.code !in 300..399 || location.isNullOrBlank()) return region
                current = url.resolve(location)?.toString()
                    ?: throw IOException("Xiaomi Passport вернул неверный redirect")
            }
        }
        throw IOException("Xiaomi Passport превысил лимит redirect")
    }

    private fun execute(
        http: OkHttpClient,
        url: String,
        cookieOverride: String? = null,
    ) = try {
        http.newCall(
            Request.Builder()
                .url(url)
                .header("User-Agent", XiaomiCloudClient.USER_AGENT)
                .apply { cookieOverride?.let { header("Cookie", it) } }
                .get()
                .build(),
        ).execute()
    } catch (error: IOException) {
        throw XiaomiCloudException.Network(error)
    }

    private fun client(jar: MemoryCookieJar): OkHttpClient =
        baseHttp.newBuilder().cookieJar(jar).build()

    private fun absoluteAccountUrl(value: String): String =
        if (value.startsWith("https://")) value else ACCOUNT_URL.resolve(value)?.toString()
            ?: throw IOException("Xiaomi Passport вернул неверный URL")

    companion object {
        private const val SID = "miothealth"
        private const val STS_HOST = "sts-hlth.io.mi.com"
        private const val STS_CALLBACK = "https://sts-hlth.io.mi.com/healthapp/sts"
        internal const val LOGIN_URL =
            "https://account.xiaomi.com/pass/serviceLogin?sid=miothealth&callback=" +
                "https%3A%2F%2Fsts-hlth.io.mi.com%2Fhealthapp%2Fsts&_locale=ru_RU"
        private val ACCOUNT_URL = "https://account.xiaomi.com/".toHttpUrl()
        private val STS_URL = "https://sts-hlth.io.mi.com/".toHttpUrl()

        private fun newDeviceId(): String {
            val bytes = ByteArray(16).also(SecureRandom()::nextBytes)
            return "wb_" + bytes.joinToString("") { "%02x".format(it) }
        }

        internal fun isAllowedLoginNavigation(url: String): Boolean = runCatching {
            val parsed = URI(url)
            parsed.scheme == "https" && (
                parsed.host == "account.xiaomi.com" || parsed.host == STS_HOST
            )
        }.getOrDefault(false)

        internal fun isStsCallback(url: String): Boolean = runCatching {
            val parsed = url.toHttpUrl()
            parsed.isHttps && parsed.host == STS_HOST && parsed.encodedPath.startsWith("/healthapp/")
        }.getOrDefault(false)

        internal fun preferRotatedPassToken(
            oldPassToken: String,
            newPassToken: String?,
            rePassTokenHeader: String?,
        ): String {
            val candidate = newPassToken?.takeIf(String::isNotBlank) ?: return oldPassToken
            if (candidate == oldPassToken) return oldPassToken
            val proof = rePassTokenHeader?.trim().orEmpty()
            if (proof.isNotEmpty()) {
                val expected = MessageDigest.getInstance("MD5")
                    .digest(oldPassToken.toByteArray(Charsets.UTF_8))
                    .joinToString("") { "%02x".format(it) }
                    .uppercase()
                if (proof.uppercase() != expected) return oldPassToken
            }
            return candidate
        }

        private fun requireAllowedPassportHost(url: HttpUrl) {
            require(url.isHttps && (url.host == "account.xiaomi.com" || url.host == STS_HOST)) {
                "Xiaomi Passport redirect заблокирован"
            }
        }
    }
}

private class MemoryCookieJar : CookieJar {
    private val cookies = mutableListOf<Cookie>()

    @Synchronized
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        cookies.forEach { incoming ->
            this.cookies.removeAll { it.name == incoming.name && it.domain == incoming.domain && it.path == incoming.path }
            if (incoming.expiresAt > System.currentTimeMillis()) this.cookies += incoming
        }
    }

    @Synchronized
    override fun loadForRequest(url: HttpUrl): List<Cookie> = cookies.filter { it.matches(url) }

    @Synchronized
    fun seed(url: HttpUrl, header: String?) {
        header.orEmpty().split(';').map(String::trim).forEach { item ->
            val separator = item.indexOf('=')
            if (separator <= 0) return@forEach
            val name = item.substring(0, separator).trim()
            val value = item.substring(separator + 1).trim()
            if (name.isBlank() || value.isBlank()) return@forEach
            runCatching {
                Cookie.Builder().name(name).value(value).hostOnlyDomain(url.host).path("/").build()
            }.getOrNull()?.let { cookie -> saveFromResponse(url, listOf(cookie)) }
        }
    }

    @Synchronized
    fun value(name: String): String? =
        cookies.lastOrNull { it.name == name && it.expiresAt > System.currentTimeMillis() }?.value
}

private fun JsonObject.string(key: String): String =
    get(key)?.jsonPrimitive?.content.orEmpty()
