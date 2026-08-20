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

class AmigoSyncApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.preferences.prepareHourlyHeartRateReplay()
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

    suspend fun sync(maxPagesPerType: Int): SyncSummary = syncMutex.withLock {
        val active = coordinator ?: error("Health Connect is unavailable")
        active.syncAll(maxPagesPerType)
    }

    suspend fun resetPairing() = syncMutex.withLock {
        pairingResetter.reset()
    }

    suspend fun resetSnapshotsAfterPermissionsChange() = syncMutex.withLock {
        preferences.resetAllSnapshots()
    }
}
