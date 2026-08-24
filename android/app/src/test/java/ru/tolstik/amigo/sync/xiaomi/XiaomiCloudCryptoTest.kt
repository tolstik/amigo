package ru.tolstik.amigo.sync.xiaomi

import java.security.MessageDigest
import java.time.Instant
import java.util.Base64
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class XiaomiCloudCryptoTest {
    @Test
    fun skippedRc4MatchesPinnedMitReferenceVector() {
        val key = Base64.getDecoder().decode("1pBrTAq6mHpeOnEOvny8EWXI5Qdep3FZlr5fllYk5K0=")
        val encrypted = SkippedRc4(key).process("hello world".toByteArray())
        assertEquals("/qsdcUhDyVUee+M=", Base64.getEncoder().encodeToString(encrypted))
    }

    @Test
    fun signedNonceAndRoundTripMatchPinnedMitReferenceVector() {
        val ssecurity = Base64.getEncoder().encodeToString("test-ssecurity-16b".toByteArray())
        val nonce = Base64.getEncoder().encodeToString("12bytesnonce".toByteArray())
        val plaintext = """{"key":"heart_rate","limit":50}"""
        val crypto = XiaomiCloudCrypto()
        val request = crypto.build(
            path = "/app/v1/data/get_latest_fitness_data",
            ssecurity = ssecurity,
            plaintext = plaintext,
            fixedNonce = nonce,
        )
        assertEquals("1pBrTAq6mHpeOnEOvny8EWXI5Qdep3FZlr5fllYk5K0=", request.signedNonce)
        assertArrayEquals(plaintext.toByteArray(), crypto.decrypt(request.signedNonce, request.data))
    }

    @Test
    fun loginNavigationIsHttpsAndStrictlyAllowlisted() {
        assertTrue(
            XiaomiPassportClient.isAllowedLoginNavigation(
                "https://account.xiaomi.com/pass/serviceLogin",
            ),
        )
        assertTrue(
            XiaomiPassportClient.isAllowedLoginNavigation(
                "https://sts-hlth.io.mi.com/healthapp/sts?d=wb_1",
            ),
        )
        assertFalse(XiaomiPassportClient.isAllowedLoginNavigation("http://account.xiaomi.com/pass"))
        assertFalse(XiaomiPassportClient.isAllowedLoginNavigation("https://account.xiaomi.com.evil.test/"))
        assertFalse(XiaomiPassportClient.isStsCallback("https://sts-hlth.io.mi.com/not-health/sts"))
    }

    @Test
    fun loginResourcesAllowCurrentXiaomiPassportCdnHostsOnly() {
        assertTrue(
            XiaomiPassportClient.isAllowedLoginResourceHost(
                "cdn.web-global.fds.api.mi-img.com",
            ),
        )
        assertTrue(
            XiaomiPassportClient.isAllowedLoginResourceHost(
                "font.sec.miui.com",
            ),
        )
        assertTrue(
            XiaomiPassportClient.isAllowedLoginResourceHost(
                "ssl-cdn.static.browser.mi-img.com",
            ),
        )
        assertTrue(
            XiaomiPassportClient.isAllowedLoginResourceHost(
                "mcfe--account-static-legacy.cnbj1.mi-fds.com",
            ),
        )
        assertFalse(
            XiaomiPassportClient.isAllowedLoginResourceHost("cdn.web-global.fds.api.evil.test"),
        )
        assertFalse(XiaomiPassportClient.isAllowedLoginResourceHost("example.com"))
    }

    @Test
    fun refreshAcceptsOnlyProvenPassTokenRotation() {
        val old = "old-pass-token"
        val proof = MessageDigest.getInstance("MD5")
            .digest(old.toByteArray())
            .joinToString("") { "%02x".format(it) }
            .uppercase()
        assertEquals(
            "new-pass-token",
            XiaomiPassportClient.preferRotatedPassToken(old, "new-pass-token", proof),
        )
        assertEquals(
            old,
            XiaomiPassportClient.preferRotatedPassToken(old, "new-pass-token", "wrong-proof"),
        )
    }

    @Test
    fun completedRefreshNeverMovesTheHistoricalCursorForward() {
        val floor = Instant.parse("2000-01-01T00:00:00Z")
        val refreshStart = Instant.parse("2026-08-21T00:00:00Z")
        assertEquals(floor, earlierHistoryEnd(floor, refreshStart))
        assertEquals(refreshStart, earlierHistoryEnd(null, refreshStart))
    }

    @Test
    fun regionChoiceToleratesAuthFailureFromWrongRegion() {
        val old = Instant.parse("2026-08-20T00:00:00Z")
        val fresh = Instant.parse("2026-08-24T00:00:00Z")
        assertEquals(
            "ru",
            chooseXiaomiRegion(
                listOf(
                    XiaomiRegionProbe("sg", false, authRequired = true, latest = null),
                    XiaomiRegionProbe("de", true, authRequired = false, latest = old),
                    XiaomiRegionProbe("ru", true, authRequired = false, latest = fresh),
                ),
                previousRegion = "sg",
            ),
        )
    }
}
