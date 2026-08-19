package ru.tolstik.amigo.sync.wire

import java.nio.charset.StandardCharsets
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.time.Instant
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import ru.tolstik.amigo.sync.crypto.P256Signatures
import ru.tolstik.amigo.sync.crypto.SignatureInput
import ru.tolstik.amigo.sync.sync.BatchEnvelope
import ru.tolstik.amigo.sync.sync.BatchMode
import ru.tolstik.amigo.sync.sync.ExportRecord
import ru.tolstik.amigo.sync.sync.RecordType

class CanonicalJsonTest {
    @Test
    fun objectKeysAndRecordsAreSerializedDeterministically() {
        val first = record(
            id = "b",
            values = JsonObject(linkedMapOf("z" to JsonPrimitive(2), "a" to JsonPrimitive("x"))),
        )
        val second = record(
            id = "a",
            values = JsonObject(linkedMapOf("a" to JsonPrimitive("x"), "z" to JsonPrimitive(2))),
        )
        val envelopeOne = batch(listOf(first, second))
        val envelopeTwo = batch(listOf(second, first))

        val encodedOne = CanonicalJson.encode(envelopeOne.toJson())
        val encodedTwo = CanonicalJson.encode(envelopeTwo.toJson())

        assertArrayEquals(encodedOne, encodedTwo)
        val text = encodedOne.toString(StandardCharsets.UTF_8)
        assertTrue(text.startsWith("{\"batch_id\":\"batch-1\",\"data_as_of\":"))
        assertTrue(text.indexOf("\"record_id\":\"a\"") < text.indexOf("\"record_id\":\"b\""))
        assertTrue(text.contains("\"values\":{\"a\":\"x\",\"z\":2}"))
    }

    @Test
    fun signingInputHasAnExactStablePrefixAndValidP256Signature() {
        val body = "{\"ok\":true}".toByteArray(StandardCharsets.UTF_8)
        val input = SignatureInput.create(1_725_000_000, "nonce-1", "batch-1", body)
        assertEquals(
            "1725000000\nnonce-1\nbatch-1\n{\"ok\":true}",
            input.toString(StandardCharsets.UTF_8),
        )

        val pair = KeyPairGenerator.getInstance("EC").run {
            initialize(ECGenParameterSpec("secp256r1"))
            generateKeyPair()
        }
        val signature = P256Signatures.sign(pair.private, input)
        val valid = Signature.getInstance("SHA256withECDSA").run {
            initVerify(pair.public)
            update(input)
            verify(signature)
        }
        assertTrue(valid)
    }

    private fun record(id: String, values: JsonObject) = ExportRecord(
        recordId = id,
        type = RecordType.STEPS,
        startTime = Instant.parse("2026-08-19T10:00:00Z"),
        endTime = Instant.parse("2026-08-19T10:05:00Z"),
        dataOrigin = "com.example.health",
        lastModifiedTime = Instant.parse("2026-08-19T10:06:00Z"),
        values = values,
    )

    private fun batch(records: List<ExportRecord>) = BatchEnvelope(
        batchId = "batch-1",
        mode = BatchMode.CHANGES,
        recordType = RecordType.STEPS,
        dataOrigin = "com.example.health",
        dataAsOf = Instant.parse("2026-08-19T10:06:00Z"),
        records = records,
    )
}
