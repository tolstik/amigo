package ru.tolstik.amigo.sync.xiaomi

import android.content.SharedPreferences
import java.lang.reflect.Proxy

internal fun memoryXiaomiPreferences(state: MutableMap<String, Any>): SharedPreferences {
    return Proxy.newProxyInstance(SharedPreferences::class.java.classLoader, arrayOf(SharedPreferences::class.java)) { _, method, args ->
        when (method.name) {
            "getString", "getInt", "getLong", "getBoolean" -> state[args!![0] as String] ?: args[1]
            "contains" -> state.containsKey(args!![0])
            "getAll" -> state.toMap()
            "edit" -> {
                val pending = state.toMutableMap()
                Proxy.newProxyInstance(SharedPreferences.Editor::class.java.classLoader, arrayOf(SharedPreferences.Editor::class.java)) { editor, action, values ->
                    when (action.name) {
                        "putString", "putInt", "putLong", "putBoolean" -> { pending[values!![0] as String] = values[1]; editor }
                        "remove" -> { pending.remove(values!![0] as String); editor }
                        "clear" -> { pending.clear(); editor }
                        "commit", "apply" -> { state.clear(); state.putAll(pending); if (action.name == "commit") true else null }
                        else -> error("Unexpected editor method ${action.name}")
                    }
                }
            }
            else -> error("Unexpected preferences method ${method.name}")
        }
    } as SharedPreferences
}
