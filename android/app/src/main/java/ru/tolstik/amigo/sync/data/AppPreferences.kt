package ru.tolstik.amigo.sync.data

import android.content.Context
import android.content.SharedPreferences
import java.time.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import ru.tolstik.amigo.sync.sync.RecordType
import ru.tolstik.amigo.sync.sync.SnapshotCursor
import ru.tolstik.amigo.sync.sync.SyncStateStore
import ru.tolstik.amigo.sync.wire.CanonicalJson

data class DeviceRegistration(
    val serverUrl: String,
    val deviceId: String,
    val pairingCode: String,
    val status: String,
)

data class LocalStatus(
    val serverUrl: String,
    val deviceLabel: String,
    val registration: DeviceRegistration?,
    val discoveredOrigins: Set<String>,
    val selectedOrigin: String?,
    val lastSync: Instant?,
    val dataAsOf: Instant?,
    val lastError: String?,
    val completedTypes: Int,
    val backgroundLastStarted: Instant?,
    val backgroundLastFinished: Instant?,
    val backgroundResult: String?,
    val backgroundRunAttempt: Int,
)

class AppPreferences(context: Context) : SyncStateStore {
    private val values: SharedPreferences = context.getSharedPreferences("amigo_sync", Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true }

    @Synchronized
    fun status(): LocalStatus = LocalStatus(
        serverUrl = values.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL,
        deviceLabel = values.getString(KEY_DEVICE_LABEL, android.os.Build.MODEL) ?: "Android",
        registration = registration(),
        discoveredOrigins = values.getStringSet(KEY_ORIGINS, emptySet())?.toSet().orEmpty(),
        selectedOrigin = selectedOrigin(),
        lastSync = instant(KEY_LAST_SYNC),
        dataAsOf = dataAsOf(),
        lastError = values.getString(KEY_LAST_ERROR, null),
        completedTypes = RecordType.entries.count(::isSnapshotComplete),
        backgroundLastStarted = instant(KEY_BACKGROUND_STARTED),
        backgroundLastFinished = instant(KEY_BACKGROUND_FINISHED),
        backgroundResult = values.getString(KEY_BACKGROUND_RESULT, null),
        backgroundRunAttempt = values.getInt(KEY_BACKGROUND_ATTEMPT, 0),
    )

    @Synchronized
    fun prepareHourlyHeartRateReplay() {
        if (values.getInt(KEY_HEART_RATE_AGGREGATE_VERSION, 0) >= 1) return
        val editor = values.edit().putInt(KEY_HEART_RATE_AGGREGATE_VERSION, 1)
        if (registration() != null) {
            val type = RecordType.HEART_RATE
            editor.remove(syncKey(type, "cursor"))
                .remove(syncKey(type, "target"))
                .putBoolean(syncKey(type, "complete"), false)
                .putBoolean(syncKey(type, "reconcile"), true)
        }
        editor.apply()
    }

    @Synchronized
    fun markBackgroundStarted(runId: String, attempt: Int, at: Instant = Instant.now()) {
        values.edit()
            .putString(KEY_BACKGROUND_ACTIVE_RUN, runId)
            .putString(KEY_BACKGROUND_STARTED, at.toString())
            .remove(KEY_BACKGROUND_FINISHED)
            .putInt(KEY_BACKGROUND_ATTEMPT, attempt)
            .putString(KEY_BACKGROUND_RESULT, "running")
            .remove(KEY_LAST_ERROR)
            .commit()
    }

    @Synchronized
    fun markBackgroundFinished(
        runId: String,
        result: String,
        error: String? = null,
        at: Instant = Instant.now(),
    ): Boolean {
        if (values.getString(KEY_BACKGROUND_ACTIVE_RUN, null) != runId) return false
        return values.edit()
            .remove(KEY_BACKGROUND_ACTIVE_RUN)
            .putString(KEY_BACKGROUND_FINISHED, at.toString())
            .putString(KEY_BACKGROUND_RESULT, result.take(80))
            .apply {
                if (error == null) remove(KEY_LAST_ERROR)
                else putString(KEY_LAST_ERROR, error.take(300))
            }
            .commit()
    }

    fun setServerUrl(value: String) {
        values.edit().putString(KEY_SERVER_URL, value.trim()).apply()
    }

    fun setDeviceLabel(value: String) {
        values.edit().putString(KEY_DEVICE_LABEL, value.trim()).apply()
    }

    fun setDiscoveredOrigins(origins: Set<String>) {
        values.edit().putStringSet(KEY_ORIGINS, origins.toSet()).apply()
    }

    fun markHealthPermissionsObserved(at: Instant = Instant.now()) {
        if (!values.contains(KEY_FIRST_HEALTH_PERMISSION_AT)) {
            values.edit().putString(KEY_FIRST_HEALTH_PERMISSION_AT, at.toString()).apply()
        }
    }

    fun fallbackHistoryFloor(): Instant =
        instant(KEY_FIRST_HEALTH_PERMISSION_AT)?.minusSeconds(30L * 24 * 60 * 60)
            ?: Instant.now().minusSeconds(30L * 24 * 60 * 60)

    fun resetAllSnapshots() {
        values.edit().also { editor ->
            RecordType.entries.forEach { type ->
                val requiresFullReconcile =
                    values.getBoolean(syncKey(type, "reconcile"), false) ||
                        values.getBoolean(syncKey(type, "complete"), false) ||
                        values.contains(syncKey(type, "cursor"))
                editor.remove(syncKey(type, "cursor"))
                editor.remove(syncKey(type, "target"))
                editor.putBoolean(syncKey(type, "complete"), false)
                if (requiresFullReconcile) {
                    editor.putBoolean(syncKey(type, "reconcile"), true)
                } else {
                    editor.remove(syncKey(type, "reconcile"))
                }
            }
        }.apply()
    }

    @Synchronized
    fun selectOrigin(origin: String) {
        require(origin.isNotBlank())
        if (registration() != null && selectedOrigin() != origin) {
            throw IllegalStateException("Reset pairing before changing the Health Connect source")
        }
        if (selectedOrigin() == origin) return
        clearSyncState(values.edit()).putString(KEY_SELECTED_ORIGIN, origin).apply()
    }

    @Synchronized
    fun saveRegistration(registration: DeviceRegistration) {
        values.edit()
            .putString(KEY_SERVER_URL, registration.serverUrl)
            .putString(KEY_DEVICE_ID, registration.deviceId)
            .putString(KEY_PAIRING_CODE, registration.pairingCode)
            .putString(KEY_PAIRING_STATUS, registration.status)
            .apply()
    }

    fun updatePairingStatus(status: String, pairingCode: String? = null) {
        values.edit()
            .putString(KEY_PAIRING_STATUS, status)
            .apply {
                when {
                    !pairingCode.isNullOrBlank() -> putString(KEY_PAIRING_CODE, pairingCode)
                    status != "pending" -> remove(KEY_PAIRING_CODE)
                }
            }
            .apply()
    }

    @Synchronized
    fun resetPairing() {
        clearSyncState(
            values.edit()
                .remove(KEY_DEVICE_ID)
                .remove(KEY_PAIRING_CODE)
                .remove(KEY_PAIRING_STATUS),
        ).apply()
    }

    fun registration(): DeviceRegistration? {
        val id = values.getString(KEY_DEVICE_ID, null)?.takeIf(String::isNotBlank) ?: return null
        return DeviceRegistration(
            serverUrl = values.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL,
            deviceId = id,
            pairingCode = values.getString(KEY_PAIRING_CODE, "") ?: "",
            status = values.getString(KEY_PAIRING_STATUS, "pending") ?: "pending",
        )
    }

    override fun selectedOrigin(): String? = values.getString(KEY_SELECTED_ORIGIN, null)

    override fun changesToken(type: RecordType): String? =
        values.getString(syncKey(type, "changes_token"), null)

    override fun setChangesToken(type: RecordType, token: String) {
        values.edit().putString(syncKey(type, "changes_token"), token).apply()
    }

    override fun snapshotCursor(type: RecordType): SnapshotCursor? {
        val raw = values.getString(syncKey(type, "cursor"), null) ?: return null
        return runCatching {
            val item = json.parseToJsonElement(raw).jsonObject
            SnapshotCursor(
                generation = item.required("generation"),
                target = Instant.parse(item.required("target")),
                rangeStart = Instant.parse(item.required("range_start")),
                rangeEnd = Instant.parse(item.required("range_end")),
                pageToken = item.optional("page_token"),
                pageIndex = item.required("page_index").toInt(),
                knownEmpty = item.required("known_empty").toBooleanStrict(),
                emptyUntil = item.optional("empty_until")?.let(Instant::parse),
                formatVersion = item.optional("format_version")?.toInt() ?: 1,
            )
        }.getOrNull()
    }

    override fun setSnapshotCursor(type: RecordType, cursor: SnapshotCursor) {
        val payload = buildJsonObject {
            put("generation", cursor.generation)
            put("format_version", cursor.formatVersion)
            put("known_empty", cursor.knownEmpty)
            cursor.emptyUntil?.let { put("empty_until", it.toString()) }
            put("page_index", cursor.pageIndex)
            cursor.pageToken?.let { put("page_token", it) }
            put("range_end", cursor.rangeEnd.toString())
            put("range_start", cursor.rangeStart.toString())
            put("target", cursor.target.toString())
        }
        values.edit()
            .putString(syncKey(type, "cursor"), CanonicalJson.render(payload))
            .putBoolean(syncKey(type, "complete"), false)
            .remove(syncKey(type, "target"))
            .apply()
    }

    override fun isSnapshotComplete(type: RecordType): Boolean =
        values.getBoolean(syncKey(type, "complete"), false)

    override fun markSnapshotComplete(type: RecordType) {
        values.edit()
            .putBoolean(syncKey(type, "complete"), true)
            .remove(syncKey(type, "cursor"))
            .remove(syncKey(type, "reconcile"))
            .remove(syncKey(type, "target"))
            .apply()
    }

    override fun snapshotRequiresFullReconcile(type: RecordType): Boolean =
        values.getBoolean(syncKey(type, "reconcile"), false)

    override fun snapshotTarget(type: RecordType): Instant? =
        values.getString(syncKey(type, "target"), null)
            ?.let { runCatching { Instant.parse(it) }.getOrNull() }

    override fun beginSnapshot(type: RecordType, target: Instant, requiresFullReconcile: Boolean) {
        val editor = values.edit()
            .putBoolean(syncKey(type, "complete"), false)
            .putString(syncKey(type, "target"), target.toString())
            .remove(syncKey(type, "cursor"))
        if (requiresFullReconcile) {
            editor.putBoolean(syncKey(type, "reconcile"), true)
        } else {
            editor.remove(syncKey(type, "reconcile"))
        }
        editor.apply()
    }

    override fun beginSnapshotWithChangesToken(
        type: RecordType,
        newToken: String,
        target: Instant,
        requiresFullReconcile: Boolean,
    ) {
        val editor = values.edit()
            .putString(syncKey(type, "changes_token"), newToken)
            .putBoolean(syncKey(type, "complete"), false)
            .putString(syncKey(type, "target"), target.toString())
            .remove(syncKey(type, "cursor"))
        if (requiresFullReconcile) {
            editor.putBoolean(syncKey(type, "reconcile"), true)
        } else {
            editor.remove(syncKey(type, "reconcile"))
        }
        editor.apply()
    }

    override fun setDataAsOf(value: Instant) {
        val current = dataAsOf()
        if (current == null || value > current) values.edit().putString(KEY_DATA_AS_OF, value.toString()).apply()
    }

    override fun dataAsOf(): Instant? = instant(KEY_DATA_AS_OF)

    override fun setLastSync(value: Instant) {
        values.edit().putString(KEY_LAST_SYNC, value.toString()).apply()
    }

    override fun setLastError(value: String?) {
        values.edit().apply {
            if (value == null) remove(KEY_LAST_ERROR) else putString(KEY_LAST_ERROR, value.take(300))
        }.apply()
    }

    private fun instant(key: String): Instant? =
        values.getString(key, null)?.let { runCatching { Instant.parse(it) }.getOrNull() }

    private fun syncKey(type: RecordType, suffix: String) = "sync.${type.wireName}.$suffix"

    private fun clearSyncState(editor: SharedPreferences.Editor): SharedPreferences.Editor {
        values.all.keys.filter { it.startsWith("sync.") }.forEach(editor::remove)
        return editor.remove(KEY_LAST_SYNC).remove(KEY_DATA_AS_OF).remove(KEY_LAST_ERROR)
    }

    companion object {
        const val DEFAULT_SERVER_URL = "https://amigo.tolstik.ru"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_DEVICE_LABEL = "device_label"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_PAIRING_CODE = "pairing_code"
        private const val KEY_PAIRING_STATUS = "pairing_status"
        private const val KEY_ORIGINS = "health_origins"
        private const val KEY_SELECTED_ORIGIN = "selected_origin"
        private const val KEY_LAST_SYNC = "last_sync"
        private const val KEY_DATA_AS_OF = "data_as_of"
        private const val KEY_LAST_ERROR = "last_error"
        private const val KEY_FIRST_HEALTH_PERMISSION_AT = "first_health_permission_at"
        private const val KEY_HEART_RATE_AGGREGATE_VERSION = "heart_rate_aggregate_version"
        private const val KEY_BACKGROUND_STARTED = "background_started"
        private const val KEY_BACKGROUND_FINISHED = "background_finished"
        private const val KEY_BACKGROUND_RESULT = "background_result"
        private const val KEY_BACKGROUND_ATTEMPT = "background_attempt"
        private const val KEY_BACKGROUND_ACTIVE_RUN = "background_active_run"
    }
}

private fun kotlinx.serialization.json.JsonObject.required(key: String): String =
    getValue(key).jsonPrimitive.content

private fun kotlinx.serialization.json.JsonObject.optional(key: String): String? =
    get(key)?.jsonPrimitive?.content
