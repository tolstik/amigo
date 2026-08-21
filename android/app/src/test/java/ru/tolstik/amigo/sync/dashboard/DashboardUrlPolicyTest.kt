package ru.tolstik.amigo.sync.dashboard

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DashboardUrlPolicyTest {
    @Test
    fun acceptsOnlyKnownDashboardRoutesOnTheCanonicalOrigin() {
        assertTrue(DashboardUrlPolicy.isAllowedNavigation("https://amigo.tolstik.ru/amigo/"))
        assertTrue(DashboardUrlPolicy.isAllowedNavigation("https://amigo.tolstik.ru/amigo/labs"))
        assertTrue(
            DashboardUrlPolicy.isAllowedNavigation(
                "https://amigo.tolstik.ru/amigo/labs/documents/" +
                    "20000000-0000-0000-0000-000000000001",
            ),
        )
        assertTrue(
            DashboardUrlPolicy.isAllowedNavigation(
                "https://amigo.tolstik.ru/amigo/labs/documents/" +
                    "20000000-0000-0000-0000-000000000001/view",
            ),
        )
        assertTrue(
            DashboardUrlPolicy.isAllowedNavigation(
                "https://amigo.tolstik.ru/amigo/studies/" +
                    "20000000-0000-0000-0000-000000000001/view",
            ),
        )
        assertFalse(DashboardUrlPolicy.isAllowedNavigation("http://amigo.tolstik.ru/amigo/"))
        assertFalse(DashboardUrlPolicy.isAllowedNavigation("https://amigo.tolstik.ru.evil.test/amigo/"))
        assertFalse(DashboardUrlPolicy.isAllowedNavigation("https://amigo.tolstik.ru/amigo/api/v1/overview"))
        assertFalse(DashboardUrlPolicy.isAllowedNavigation("javascript:alert(1)"))
        assertTrue(DashboardUrlPolicy.isInternalErrorPage("chrome-error://chromewebdata/"))
        assertFalse(DashboardUrlPolicy.isInternalErrorPage("https://evil.test/chrome-error"))
    }

    @Test
    fun normalizesVerifiedLinksWithoutAcceptingQueriesOrLookalikePaths() {
        assertEquals(
            DashboardUrlPolicy.ROOT_URL,
            DashboardUrlPolicy.normalizeAppLink("https://amigo.tolstik.ru/amigo"),
        )
        assertEquals(
            "https://amigo.tolstik.ru/amigo/profile#privacy",
            DashboardUrlPolicy.normalizeAppLink("https://amigo.tolstik.ru/amigo/profile#privacy"),
        )
        assertNull(DashboardUrlPolicy.normalizeAppLink("https://amigo.tolstik.ru/amigoevil"))
        assertNull(DashboardUrlPolicy.normalizeAppLink("https://amigo.tolstik.ru/amigo/?next=evil"))
    }

    @Test
    fun downloadAllowlistDoesNotPermitCookieForwardingElsewhere() {
        assertTrue(
            DashboardUrlPolicy.isAllowedDownload(
                "https://amigo.tolstik.ru/amigo/api/v1/export/weight.csv?range=program",
            ),
        )
        assertTrue(
            DashboardUrlPolicy.isAllowedDownload(
                "https://amigo.tolstik.ru/amigo/api/v1/studies/documents/" +
                    "20000000-0000-0000-0000-000000000001/download",
            ),
        )
        assertTrue(
            DashboardUrlPolicy.isAllowedDownload(
                "https://amigo.tolstik.ru/amigo/api/v1/export/activity.csv?range=1y",
            ),
        )
        assertTrue(
            DashboardUrlPolicy.isAllowedDownload(
                "https://amigo.tolstik.ru/amigo/api/v1/labs/documents/" +
                    "20000000-0000-0000-0000-000000000001/download",
            ),
        )
        assertFalse(
            DashboardUrlPolicy.isAllowedDownload(
                "https://amigo.tolstik.ru/amigo/api/v1/export/weight.csv?next=https://evil.test",
            ),
        )
        assertFalse(
            DashboardUrlPolicy.isAllowedDownload(
                "https://evil.test/amigo/api/v1/export/weight.csv?range=program",
            ),
        )
        assertFalse(
            DashboardUrlPolicy.isAllowedDownload(
                "https://amigo.tolstik.ru/amigo/api/v1/export/weight.csv?range=7d",
            ),
        )
    }
}
