package ru.tolstik.amigo.sync.health

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.changes.DeletionChange
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.metadata.DataOrigin
import androidx.health.connect.client.request.ChangesTokenRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.response.ReadRecordsResponse
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Duration
import java.time.Instant
import kotlin.reflect.KClass
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import ru.tolstik.amigo.sync.sync.ChangePage
import ru.tolstik.amigo.sync.sync.ExportChange
import ru.tolstik.amigo.sync.sync.ExportRecord
import ru.tolstik.amigo.sync.sync.HealthDataSource
import ru.tolstik.amigo.sync.sync.RecordType
import ru.tolstik.amigo.sync.sync.SnapshotPage

data class HealthPermissionStatus(
    val requested: Set<String>,
    val granted: Set<String>,
    val historyAvailable: Boolean,
    val backgroundAvailable: Boolean,
) {
    val allGranted: Boolean get() = granted.containsAll(requested)
    val historyGranted: Boolean
        get() = !historyAvailable || HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY in granted
    val backgroundGranted: Boolean
        get() = !backgroundAvailable ||
            HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND in granted
}

class HealthConnectGateway(
    private val context: Context,
    private val client: HealthConnectClient,
    private val fallbackHistoryFloor: () -> Instant = { Instant.now().minus(Duration.ofDays(30)) },
) : HealthDataSource {
    override suspend fun enabledTypes(): List<RecordType> {
        val granted = client.permissionController.getGrantedPermissions()
        return definitions
            .filter { HealthPermission.getReadPermission(it.recordClass) in granted }
            .map(Definition::type)
    }

    override suspend fun accessibleHistoryFloor(requestedFloor: Instant, asOf: Instant): Instant {
        val granted = client.permissionController.getGrantedPermissions()
        val hasExtendedHistory =
            featureAvailable(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_HISTORY) &&
                HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY in granted
        return if (hasExtendedHistory) requestedFloor else maxOf(requestedFloor, fallbackHistoryFloor())
    }

    suspend fun permissionStatus(): HealthPermissionStatus {
        val historyAvailable = featureAvailable(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_HISTORY)
        val backgroundAvailable = featureAvailable(
            HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND,
        )
        val requested = buildSet {
            definitions.forEach { add(HealthPermission.getReadPermission(it.recordClass)) }
            if (historyAvailable) add(HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY)
            if (backgroundAvailable) add(HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND)
        }
        return HealthPermissionStatus(
            requested = requested,
            granted = client.permissionController.getGrantedPermissions(),
            historyAvailable = historyAvailable,
            backgroundAvailable = backgroundAvailable,
        )
    }

    fun permissionsToRequest(): Set<String> = buildSet {
        definitions.forEach { add(HealthPermission.getReadPermission(it.recordClass)) }
        if (featureAvailable(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_HISTORY)) {
            add(HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY)
        }
        if (featureAvailable(HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND)) {
            add(HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND)
        }
    }

    suspend fun discoverOrigins(now: Instant = Instant.now()): Set<String> {
        val origins = linkedSetOf<String>()
        val from = now.minus(Duration.ofDays(30))
        definitions.forEach { definition ->
            runCatching {
                var pageToken: String? = null
                do {
                    val response = readRaw(
                        type = definition.type,
                        from = from,
                        until = now,
                        origin = null,
                        pageToken = pageToken,
                        pageSize = pageSize(definition.type),
                    )
                    response.records.mapTo(origins) { it.metadata.dataOrigin.packageName }
                    pageToken = response.pageToken
                } while (pageToken != null)
            }
        }
        return origins.filter(String::isNotBlank).toSet()
    }

    override suspend fun findEarliest(
        type: RecordType,
        dataOrigin: String,
        from: Instant,
        until: Instant,
    ): Instant? = readRaw(
        type = type,
        from = from,
        until = until,
        origin = dataOrigin,
        pageToken = null,
        pageSize = 1,
    ).records.firstOrNull()?.startInstant()

    override suspend fun readSnapshotPage(
        type: RecordType,
        dataOrigin: String,
        from: Instant,
        until: Instant,
        pageToken: String?,
    ): SnapshotPage {
        val response = readRaw(type, from, until, dataOrigin, pageToken, pageSize(type))
        return SnapshotPage(
            records = response.records.map(::export),
            nextPageToken = response.pageToken,
        )
    }

    override suspend fun createChangesToken(type: RecordType, dataOrigin: String): String =
        client.getChangesToken(
            ChangesTokenRequest(
                recordTypes = setOf(definition(type).recordClass),
                dataOriginFilters = setOf(DataOrigin(dataOrigin)),
            ),
        )

    override suspend fun readChanges(
        type: RecordType,
        dataOrigin: String,
        token: String,
    ): ChangePage {
        val response = client.getChanges(token)
        val changes = response.changes.mapNotNull { change ->
            when (change) {
                is UpsertionChange -> {
                    if (change.record.metadata.dataOrigin.packageName == dataOrigin) {
                        ExportChange.Upsert(export(change.record))
                    } else {
                        null
                    }
                }
                is DeletionChange -> ExportChange.Delete(change.recordId)
                else -> null
            }
        }
        return ChangePage(
            changes = changes,
            nextChangesToken = response.nextChangesToken,
            hasMore = response.hasMore,
            tokenExpired = response.changesTokenExpired,
        )
    }

    private fun featureAvailable(feature: Int): Boolean =
        client.features.getFeatureStatus(feature) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE

    @Suppress("UNCHECKED_CAST")
    private suspend fun readRaw(
        type: RecordType,
        from: Instant,
        until: Instant,
        origin: String?,
        pageToken: String?,
        pageSize: Int,
    ): ReadRecordsResponse<Record> {
        val recordClass = definition(type).recordClass as KClass<Record>
        return client.readRecords(
            ReadRecordsRequest(
                recordType = recordClass,
                timeRangeFilter = TimeRangeFilter.between(from, until),
                dataOriginFilter = origin?.let { setOf(DataOrigin(it)) }.orEmpty(),
                ascendingOrder = true,
                pageSize = pageSize,
                pageToken = pageToken,
            ),
        )
    }

    private fun export(record: Record): ExportRecord {
        val type = definitions.firstOrNull { it.recordClass.isInstance(record) }?.type
            ?: error("Unsupported Health Connect record: ${record::class.java.simpleName}")
        val start = record.startInstant()
        val end = record.endInstant()
        return ExportRecord(
            recordId = record.metadata.id,
            type = type,
            startTime = start,
            endTime = end,
            dataOrigin = record.metadata.dataOrigin.packageName,
            lastModifiedTime = record.metadata.lastModifiedTime,
            values = recordValues(record),
        )
    }

    private fun recordValues(record: Record): JsonObject = buildJsonObject {
        put("recording_method", record.metadata.recordingMethod)
        when (record) {
            is StepsRecord -> {
                put("count", record.count)
                putOffsets(record.startZoneOffset, record.endZoneOffset)
            }
            is DistanceRecord -> {
                put("meters", record.distance.inMeters)
                putOffsets(record.startZoneOffset, record.endZoneOffset)
            }
            is ActiveCaloriesBurnedRecord -> {
                put("kilocalories", record.energy.inKilocalories)
                putOffsets(record.startZoneOffset, record.endZoneOffset)
            }
            is TotalCaloriesBurnedRecord -> {
                put("kilocalories", record.energy.inKilocalories)
                putOffsets(record.startZoneOffset, record.endZoneOffset)
            }
            is ExerciseSessionRecord -> {
                put("exercise_type", record.exerciseType)
                put("duration_seconds", Duration.between(record.startTime, record.endTime).seconds)
                putOffsets(record.startZoneOffset, record.endZoneOffset)
            }
            is SleepSessionRecord -> {
                put("duration_seconds", Duration.between(record.startTime, record.endTime).seconds)
                put(
                    "stages",
                    JsonArray(record.stages.sortedBy { it.startTime }.map { stage ->
                        buildJsonObject {
                            put("end_time", stage.endTime.toString())
                            put("stage", sleepStage(stage.stage))
                            put("start_time", stage.startTime.toString())
                        }
                    }),
                )
                putOffsets(record.startZoneOffset, record.endZoneOffset)
            }
            is HeartRateRecord -> {
                put(
                    "samples",
                    JsonArray(record.samples.sortedBy { it.time }.map { sample ->
                        buildJsonObject {
                            put("beats_per_minute", sample.beatsPerMinute)
                            put("time", sample.time.toString())
                        }
                    }),
                )
                putOffsets(record.startZoneOffset, record.endZoneOffset)
            }
            is RestingHeartRateRecord -> {
                put("beats_per_minute", record.beatsPerMinute)
                record.zoneOffset?.let { put("zone_offset_seconds", it.totalSeconds) }
            }
            is HeartRateVariabilityRmssdRecord -> {
                put("rmssd_millis", record.heartRateVariabilityMillis)
                record.zoneOffset?.let { put("zone_offset_seconds", it.totalSeconds) }
            }
            is OxygenSaturationRecord -> {
                put("percentage", record.percentage.value)
                record.zoneOffset?.let { put("zone_offset_seconds", it.totalSeconds) }
            }
            is Vo2MaxRecord -> {
                put("milliliters_per_minute_kilogram", record.vo2MillilitersPerMinuteKilogram)
                put("measurement_method", record.measurementMethod)
                record.zoneOffset?.let { put("zone_offset_seconds", it.totalSeconds) }
            }
        }
    }

    private fun kotlinx.serialization.json.JsonObjectBuilder.putOffsets(
        start: java.time.ZoneOffset?,
        end: java.time.ZoneOffset?,
    ) {
        start?.let { put("start_zone_offset_seconds", it.totalSeconds) }
        end?.let { put("end_zone_offset_seconds", it.totalSeconds) }
    }

    private fun Record.startInstant(): Instant = when (this) {
        is StepsRecord -> startTime
        is DistanceRecord -> startTime
        is ActiveCaloriesBurnedRecord -> startTime
        is TotalCaloriesBurnedRecord -> startTime
        is ExerciseSessionRecord -> startTime
        is SleepSessionRecord -> startTime
        is HeartRateRecord -> startTime
        is RestingHeartRateRecord -> time
        is HeartRateVariabilityRmssdRecord -> time
        is OxygenSaturationRecord -> time
        is Vo2MaxRecord -> time
        else -> error("Unsupported Health Connect time model: ${this::class.java.simpleName}")
    }

    private fun Record.endInstant(): Instant? = when (this) {
        is StepsRecord -> endTime
        is DistanceRecord -> endTime
        is ActiveCaloriesBurnedRecord -> endTime
        is TotalCaloriesBurnedRecord -> endTime
        is ExerciseSessionRecord -> endTime
        is SleepSessionRecord -> endTime
        is HeartRateRecord -> endTime
        is RestingHeartRateRecord,
        is HeartRateVariabilityRmssdRecord,
        is OxygenSaturationRecord,
        is Vo2MaxRecord -> null
        else -> error("Unsupported Health Connect time model: ${this::class.java.simpleName}")
    }

    private fun sleepStage(value: Int): String = when (value) {
        SleepSessionRecord.STAGE_TYPE_AWAKE -> "awake"
        SleepSessionRecord.STAGE_TYPE_SLEEPING -> "sleeping"
        SleepSessionRecord.STAGE_TYPE_OUT_OF_BED -> "out_of_bed"
        SleepSessionRecord.STAGE_TYPE_LIGHT -> "light"
        SleepSessionRecord.STAGE_TYPE_DEEP -> "deep"
        SleepSessionRecord.STAGE_TYPE_REM -> "rem"
        SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED -> "awake_in_bed"
        else -> "unknown"
    }

    private fun definition(type: RecordType): Definition = definitions.first { it.type == type }

    private fun pageSize(type: RecordType): Int =
        if (type == RecordType.HEART_RATE) HEART_RATE_PAGE_SIZE else DEFAULT_PAGE_SIZE

    private data class Definition(
        val type: RecordType,
        val recordClass: KClass<out Record>,
    )

    companion object {
        private const val DEFAULT_PAGE_SIZE = 100
        private const val HEART_RATE_PAGE_SIZE = 10

        private val definitions = listOf(
            Definition(RecordType.STEPS, StepsRecord::class),
            Definition(RecordType.DISTANCE, DistanceRecord::class),
            Definition(RecordType.ACTIVE_CALORIES, ActiveCaloriesBurnedRecord::class),
            Definition(RecordType.TOTAL_CALORIES, TotalCaloriesBurnedRecord::class),
            Definition(RecordType.EXERCISE, ExerciseSessionRecord::class),
            Definition(RecordType.SLEEP, SleepSessionRecord::class),
            Definition(RecordType.HEART_RATE, HeartRateRecord::class),
            Definition(RecordType.RESTING_HEART_RATE, RestingHeartRateRecord::class),
            Definition(RecordType.HRV_RMSSD, HeartRateVariabilityRmssdRecord::class),
            Definition(RecordType.OXYGEN_SATURATION, OxygenSaturationRecord::class),
            Definition(RecordType.VO2_MAX, Vo2MaxRecord::class),
        )

        fun sdkStatus(context: Context): Int = HealthConnectClient.getSdkStatus(context)

        fun create(
            context: Context,
            fallbackHistoryFloor: () -> Instant = { Instant.now().minus(Duration.ofDays(30)) },
        ): HealthConnectGateway = HealthConnectGateway(
            context,
            HealthConnectClient.getOrCreate(context),
            fallbackHistoryFloor,
        )
    }
}
