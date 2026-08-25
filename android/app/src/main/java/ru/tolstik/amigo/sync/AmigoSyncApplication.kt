package ru.tolstik.amigo.sync

import android.app.Application
import androidx.health.connect.client.HealthConnectClient
import java.time.Duration
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.OkHttpClient
import ru.tolstik.amigo.sync.crypto.AndroidKeyStoreSigner
import ru.tolstik.amigo.sync.crypto.PairingResetter
import ru.tolstik.amigo.sync.data.AppPreferences
import ru.tolstik.amigo.sync.health.HealthConnectGateway
import ru.tolstik.amigo.sync.network.IngestApi
import ru.tolstik.amigo.sync.sync.SyncCoordinator
import ru.tolstik.amigo.sync.sync.SyncSummary
import ru.tolstik.amigo.sync.worker.SyncScheduler
import ru.tolstik.amigo.sync.xiaomi.XiaomiCredentialStore
import ru.tolstik.amigo.sync.xiaomi.XiaomiSyncCoordinator
import ru.tolstik.amigo.sync.xiaomi.XiaomiSyncMode
import ru.tolstik.amigo.sync.xiaomi.XiaomiSyncPreferences
import ru.tolstik.amigo.sync.xiaomi.XiaomiSyncSummary

class AmigoSyncApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        if (android.app.Application.getProcessName().endsWith(":xiaomi_auth")) return
        container = AppContainer(this)
        container.preferences.prepareHeartRateReconciliation()
        SyncScheduler.scheduleHourly(this)
        SyncScheduler.scheduleImmediate(this)
    }
}

class AppContainer(application: Application) {
    val preferences = AppPreferences(application)
    val signer = AndroidKeyStoreSigner()
    val http = OkHttpClient.Builder()
        .connectTimeout(Duration.ofSeconds(15))
        .readTimeout(Duration.ofSeconds(45))
        .writeTimeout(Duration.ofSeconds(45))
        .callTimeout(Duration.ofSeconds(75))
        .build()
    val ingestApi = IngestApi(http, signer, preferences)
    internal val xiaomiCredentials = XiaomiCredentialStore(application)
    internal val xiaomiPreferences = XiaomiSyncPreferences(application)
    val healthGateway: HealthConnectGateway? =
        if (HealthConnectGateway.sdkStatus(application) == HealthConnectClient.SDK_AVAILABLE) {
            HealthConnectGateway.create(application, preferences::fallbackHistoryFloor)
        } else {
            null
        }

    private val coordinator = healthGateway?.let {
        SyncCoordinator(source = it, uploader = ingestApi, state = preferences)
    }
    private val pairingResetter = PairingResetter(signer, preferences::resetPairing)
    private val syncMutex = Mutex()
    private val xiaomiCoordinator = XiaomiSyncCoordinator(
        credentialsStore = xiaomiCredentials,
        preferences = xiaomiPreferences,
        ingest = ingestApi,
        http = http,
    )

    suspend fun sync(maxPagesPerType: Int): SyncSummary = syncMutex.withLock {
        val active = coordinator ?: error("Health Connect is unavailable")
        active.syncAll(maxPagesPerType)
    }

    suspend fun resetPairing() = syncMutex.withLock {
        pairingResetter.reset()
    }

    suspend fun enableXiaomi(sealedSession: String): XiaomiSyncSummary = syncMutex.withLock {
        xiaomiCoordinator.enableFromSealedSession(sealedSession)
    }

    internal suspend fun syncXiaomi(
        maxPages: Int,
        refreshDays: Long = 3,
        mode: XiaomiSyncMode = XiaomiSyncMode.ROUTINE,
    ): XiaomiSyncSummary = syncMutex.withLock {
        xiaomiCoordinator.sync(maxPages, refreshDays, mode)
    }

    suspend fun disableXiaomi() = syncMutex.withLock {
        xiaomiCoordinator.disable()
    }

    suspend fun resetSnapshotsAfterPermissionsChange() = syncMutex.withLock {
        preferences.resetAllSnapshots()
    }
}
