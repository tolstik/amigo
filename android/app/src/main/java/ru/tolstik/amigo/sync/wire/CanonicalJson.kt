package ru.tolstik.amigo.sync.wire

import java.nio.charset.StandardCharsets
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Stable UTF-8 JSON used for idempotent batches and signature inputs. */
object CanonicalJson {
    fun encode(element: JsonElement): ByteArray = render(element).toByteArray(StandardCharsets.UTF_8)

    fun render(element: JsonElement): String = when (element) {
        is JsonObject -> element.entries
            .sortedBy { it.key }
            .joinToString(prefix = "{", postfix = "}", separator = ",") { (key, value) ->
                "${JsonPrimitive(key)}:${render(value)}"
            }
        is JsonArray -> element.joinToString(prefix = "[", postfix = "]", separator = ",") {
            render(it)
        }
        JsonNull -> "null"
        is JsonPrimitive -> element.toString()
    }
}
