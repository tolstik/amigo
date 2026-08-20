package ru.tolstik.amigo.sync.dashboard

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

object DashboardUrlPolicy {
    const val ORIGIN = "https://amigo.tolstik.ru"
    const val ROOT_URL = "$ORIGIN/amigo/"

    private val dashboardPaths = setOf(
        "/amigo",
        "/amigo/",
        "/amigo/progress",
        "/amigo/history",
        "/amigo/pressure",
        "/amigo/composition",
        "/amigo/activity",
        "/amigo/recovery",
        "/amigo/labs",
        "/amigo/labs/upload",
        "/amigo/studies",
        "/amigo/assistant",
        "/amigo/profile",
    )
    private val labDocumentPath = Regex(
        "^/amigo/labs/documents/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-" +
            "[0-9a-f]{4}-[0-9a-f]{12}/?$",
    )
    private val labAnalytePath = Regex("^/amigo/labs/analytes/[A-Za-z0-9._~%-]+/?$")
    private val labViewerPath = Regex(
        "^/amigo/labs/documents/[0-9a-f-]{36}/view/?$",
    )
    private val studyPath = Regex("^/amigo/studies/[0-9a-f-]{36}(?:/view)?/?$")
    private val exportPath = Regex(
        "^/amigo/api/v1/export/(weight|pressure|composition|activity|recovery)\\.csv$",
    )
    private val labDownloadPath = Regex(
        "^/amigo/api/v1/labs/documents/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-" +
            "[0-9a-f]{4}-[0-9a-f]{12}/download$",
    )
    private val studyDownloadPath = Regex(
        "^/amigo/api/v1/studies/documents/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-" +
            "[0-9a-f]{4}-[0-9a-f]{12}/download$",
    )
    private val allowedRanges = setOf("30d", "90d", "1y", "program", "all")

    fun normalizeAppLink(rawUrl: String?): String? {
        val parsed = rawUrl?.toHttpUrlOrNull() ?: return null
        if (!isCanonicalOrigin(parsed) || parsed.encodedQuery != null) return null
        if (!isDashboardPath(parsed.encodedPath)) return null
        if (parsed.encodedPath == "/amigo") return ROOT_URL
        return parsed.toString()
    }

    fun isAllowedNavigation(rawUrl: String?): Boolean {
        val parsed = rawUrl?.toHttpUrlOrNull() ?: return false
        return isCanonicalOrigin(parsed) &&
            parsed.encodedQuery == null &&
            isDashboardPath(parsed.encodedPath)
    }

    fun isAllowedDownload(rawUrl: String?): Boolean {
        val parsed = rawUrl?.toHttpUrlOrNull() ?: return false
        if (!isCanonicalOrigin(parsed) || parsed.fragment != null) return false
        if (labDownloadPath.matches(parsed.encodedPath)) return parsed.encodedQuery == null
        if (studyDownloadPath.matches(parsed.encodedPath)) return parsed.encodedQuery == null
        if (!exportPath.matches(parsed.encodedPath)) return false
        if (parsed.queryParameterNames.any { it != "range" }) return false
        val ranges = parsed.queryParameterValues("range")
        return ranges.size <= 1 && ranges.all(allowedRanges::contains)
    }

    fun isCanonicalOrigin(rawUrl: String?): Boolean =
        rawUrl?.toHttpUrlOrNull()?.let(::isCanonicalOrigin) == true

    private fun isDashboardPath(path: String): Boolean {
        val normalized = path.removeSuffix("/").ifBlank { "/" }
        return path in dashboardPaths ||
            normalized in dashboardPaths ||
            labDocumentPath.matches(path) ||
            labAnalytePath.matches(path) ||
            labViewerPath.matches(path) ||
            studyPath.matches(path)
    }

    private fun isCanonicalOrigin(url: HttpUrl): Boolean =
        url.scheme == "https" &&
            url.host == "amigo.tolstik.ru" &&
            url.port == 443 &&
            url.username.isEmpty() &&
            url.password.isEmpty()
}
