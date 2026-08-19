package ru.tolstik.amigo.sync.worker

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.io.IOException
import java.util.concurrent.TimeUnit
import ru.tolstik.amigo.sync.AmigoSyncApplication

class SyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as AmigoSyncApplication).container
        val registration = container.preferences.registration() ?: return Result.success()
        if (registration.status != "approved" || container.preferences.selectedOrigin() == null) {
            return Result.success()
        }
        val health = container.healthGateway ?: return Result.success()
        val permissions = health.permissionStatus()
        if (!permissions.backgroundAvailable || !permissions.backgroundGranted) {
            container.preferences.setLastError("Background Health Connect permission is not granted")
            return Result.success()
        }
        return try {
            container.sync(maxPagesPerType = 4)
            Result.success()
        } catch (error: SecurityException) {
            container.preferences.setLastError("Health Connect permission was revoked")
            Result.failure()
        } catch (error: IOException) {
            container.preferences.setLastError("Server is temporarily unavailable")
            Result.retry()
        } catch (error: Exception) {
            container.preferences.setLastError(error.message ?: "Background sync failed")
            if (runAttemptCount < 5) Result.retry() else Result.failure()
        }
    }
}

object SyncScheduler {
    private const val UNIQUE_WORK = "amigo-health-connect-hourly"

    fun scheduleHourly(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = PeriodicWorkRequestBuilder<SyncWorker>(
            1,
            TimeUnit.HOURS,
            15,
            TimeUnit.MINUTES,
        )
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            UNIQUE_WORK,
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }
}
