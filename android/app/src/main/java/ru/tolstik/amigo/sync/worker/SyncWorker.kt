package ru.tolstik.amigo.sync.worker

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.ExistingWorkPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.io.IOException
import java.time.Duration
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import ru.tolstik.amigo.sync.AmigoSyncApplication
import ru.tolstik.amigo.sync.sync.userFacingSyncError

class SyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as AmigoSyncApplication).container
        val runId = id.toString()
        container.preferences.markBackgroundStarted(runId, runAttemptCount)
        val registration = container.preferences.registration() ?: run {
            container.preferences.markBackgroundFinished(runId, "not_paired")
            return Result.success()
        }
        if (registration.status != "approved" || container.preferences.selectedOrigin() == null) {
            container.preferences.markBackgroundFinished(runId, "not_ready")
            return Result.success()
        }
        val health = container.healthGateway ?: run {
            container.preferences.markBackgroundFinished(runId, "health_connect_unavailable")
            return Result.success()
        }
        val permissions = health.permissionStatus()
        if (!permissions.backgroundAvailable || !permissions.backgroundGranted) {
            container.preferences.markBackgroundFinished(
                runId,
                "background_permission_missing",
                "Не выдано разрешение Health Connect на фоновое чтение",
            )
            return Result.success()
        }
        return try {
            val summary = container.sync(maxPagesPerType = 4)
            val enabled = health.enabledTypes().size
            if (summary.completedTypes < enabled) {
                SyncScheduler.continueBackfill(applicationContext)
            }
            container.preferences.markBackgroundFinished(
                runId,
                if (summary.completedTypes < enabled) "backfill_continues" else "success",
            )
            Result.success()
        } catch (error: CancellationException) {
            container.preferences.markBackgroundFinished(runId, "cancelled")
            throw error
        } catch (error: SecurityException) {
            container.preferences.markBackgroundFinished(
                runId,
                "permission_revoked",
                "Разрешение Health Connect было отозвано",
            )
            Result.failure()
        } catch (error: IOException) {
            container.preferences.markBackgroundFinished(
                runId,
                "server_unavailable",
                userFacingSyncError(error),
            )
            Result.retry()
        } catch (error: Exception) {
            container.preferences.markBackgroundFinished(
                runId,
                "failed",
                "Фоновая синхронизация завершилась с ошибкой",
            )
            if (runAttemptCount < 5) Result.retry() else Result.failure()
        }
    }
}

object SyncScheduler {
    private const val UNIQUE_WORK = "amigo-health-connect-hourly"
    private const val IMMEDIATE_WORK = "amigo-health-connect-immediate"
    private const val BACKFILL_WORK = "amigo-health-connect-backfill"
    internal val backfillPolicy = ExistingWorkPolicy.APPEND_OR_REPLACE

    private fun constraints() = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()

    fun scheduleHourly(context: Context) {
        val request = PeriodicWorkRequestBuilder<SyncWorker>(
            1,
            TimeUnit.HOURS,
            15,
            TimeUnit.MINUTES,
        )
            .setConstraints(constraints())
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            UNIQUE_WORK,
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }

    fun scheduleImmediate(context: Context) {
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(constraints())
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            IMMEDIATE_WORK,
            ExistingWorkPolicy.KEEP,
            request,
        )
    }

    fun continueBackfill(context: Context) {
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(constraints())
            .setInitialDelay(Duration.ofMinutes(1))
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            BACKFILL_WORK,
            backfillPolicy,
            request,
        )
    }
}
