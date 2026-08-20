package ru.tolstik.amigo.sync.dashboard

import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.util.Locale

data class DashboardDownloadRequest(
    val url: String,
    val userAgent: String,
    val contentDisposition: String?,
    val mimeType: String?,
    val suggestedName: String,
)

object DashboardFilePolicy {
    const val MAX_LAB_UPLOAD_BYTES = 20L * 1024 * 1024
    const val MAX_DOWNLOAD_BYTES = 25L * 1024 * 1024

    val allowedUploadMimeTypes = arrayOf(
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/heif",
    )

    private val allowedExtensions = setOf("pdf", "jpg", "jpeg", "png", "heic", "heif")
    private val invalidFilenameCharacters = Regex("[\\u0000-\\u001f\\u007f<>:\"/\\\\|?*]")

    fun isAllowedUpload(displayName: String?, mimeType: String?, sizeBytes: Long?): Boolean {
        if (sizeBytes != null && (sizeBytes < 0 || sizeBytes > MAX_LAB_UPLOAD_BYTES)) return false
        val normalizedMime = mimeType?.lowercase(Locale.ROOT)?.substringBefore(';')
        if (normalizedMime in allowedUploadMimeTypes) return true
        val extension = displayName?.substringAfterLast('.', "")?.lowercase(Locale.ROOT)
        return extension in allowedExtensions
    }

    fun suggestedDownloadName(
        contentDisposition: String?,
        mimeType: String?,
        url: String,
    ): String {
        val dispositionName = contentDisposition?.let(::filenameFromDisposition)
        val pathName = url.substringBefore('?').substringAfterLast('/').takeIf { '.' in it }
        val fallback = when (mimeType?.lowercase(Locale.ROOT)?.substringBefore(';')) {
            "text/csv" -> "amigo-data.csv"
            "application/pdf" -> "amigo-document.pdf"
            "image/jpeg" -> "amigo-document.jpg"
            "image/png" -> "amigo-document.png"
            "image/heic" -> "amigo-document.heic"
            "image/heif" -> "amigo-document.heif"
            else -> "amigo-download"
        }
        return sanitizeFilename(dispositionName ?: pathName ?: fallback)
    }

    fun sanitizeFilename(raw: String): String {
        val leaf = raw.substringAfterLast('/').substringAfterLast('\\')
        val cleaned = leaf
            .replace(invalidFilenameCharacters, "_")
            .trim()
            .trim('.', ' ')
            .ifBlank { "amigo-download" }
        if (cleaned.length <= 120) return cleaned
        val extension = cleaned.substringAfterLast('.', "").takeIf { it.length in 1..10 }
        return if (extension == null) {
            cleaned.take(120)
        } else {
            cleaned.substringBeforeLast('.').take(119 - extension.length).trimEnd() + "." + extension
        }
    }

    private fun filenameFromDisposition(value: String): String? {
        val encoded = Regex("(?i)(?:^|;)\\s*filename\\*=UTF-8''([^;]+)")
            .find(value)
            ?.groupValues
            ?.get(1)
        if (!encoded.isNullOrBlank()) {
            return runCatching {
                // RFC 5987 uses percent encoding, where a literal plus is not a space.
                URLDecoder.decode(encoded.replace("+", "%2B"), StandardCharsets.UTF_8.name())
            }.getOrNull()
        }
        return Regex("(?i)(?:^|;)\\s*filename=\"([^\"]+)\"")
            .find(value)
            ?.groupValues
            ?.get(1)
            ?: Regex("(?i)(?:^|;)\\s*filename=([^;]+)")
                .find(value)
                ?.groupValues
                ?.get(1)
                ?.trim()
    }
}
