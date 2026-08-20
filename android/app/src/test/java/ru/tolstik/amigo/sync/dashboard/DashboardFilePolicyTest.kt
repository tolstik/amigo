package ru.tolstik.amigo.sync.dashboard

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DashboardFilePolicyTest {
    @Test
    fun acceptsSupportedUploadsAndEnforcesTheSizeLimit() {
        assertTrue(DashboardFilePolicy.isAllowedUpload("report.pdf", "application/pdf", 1024))
        assertTrue(DashboardFilePolicy.isAllowedUpload("photo.HEIC", null, 1024))
        assertFalse(
            DashboardFilePolicy.isAllowedUpload(
                "report.pdf",
                "application/pdf",
                DashboardFilePolicy.MAX_LAB_UPLOAD_BYTES + 1,
            ),
        )
        assertFalse(DashboardFilePolicy.isAllowedUpload("payload.html", "text/html", 1024))
    }

    @Test
    fun decodesAndSanitizesSuggestedFilenames() {
        assertEquals(
            "анализы.pdf",
            DashboardFilePolicy.suggestedDownloadName(
                "attachment; filename*=UTF-8''%D0%B0%D0%BD%D0%B0%D0%BB%D0%B8%D0%B7%D1%8B.pdf",
                "application/pdf",
                "https://amigo.tolstik.ru/download",
            ),
        )
        assertEquals("name.csv", DashboardFilePolicy.sanitizeFilename("../evil/name.csv"))
        assertEquals(
            "report+final.pdf",
            DashboardFilePolicy.suggestedDownloadName(
                "attachment; filename*=UTF-8''report+final.pdf",
                "application/pdf",
                "https://amigo.tolstik.ru/download",
            ),
        )
        assertEquals(
            "amigo-data.csv",
            DashboardFilePolicy.suggestedDownloadName(null, "text/csv", "https://amigo.tolstik.ru/export"),
        )
    }
}
