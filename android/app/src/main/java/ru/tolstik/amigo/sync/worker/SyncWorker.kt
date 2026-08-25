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
import ru.tolstik.amigo.sync.xiaomi.XiaomiCloudException
import ru.tolstik.amigo.sync.xiaomi.XiaomiSyncMode

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
        if (registration.status != "approved") {
            container.preferences.markBackgroundFinished(runId, "not_ready")
            return Result.success()
        }
        return try {
            var didWork = false
            var needsContinuation = false
            var cloudFailure: Exception? = null
            if (container.xiaomiPreferences.enabled()) {
                didWork = true
                try {
                    val cloud = container.syncXiaomi(
                        maxPages = 4,
                        refreshDays = inputData.getLong(SyncScheduler.INPUT_XIAOMI_REFRESH_DAYS, 3),
                        mode = if (
                            inputData.getBoolean(SyncScheduler.INPUT_XIAOMI_BACKFILL_CONTINUATION, false)
                        ) {
                            XiaomiSyncMode.BACKFILL_CONTINUATION
                        } else {
                            XiaomiSyncMode.ROUTINE
                        },
                    )
                    needsContinuation = cloud.needsContinuation
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    // Health Connect is retained as rollback history. Uploading it does not
                    // reactivate that source while finalised Xiaomi coverage is enabled.
                    cloudFailure = error
                }
            }
            val health = container.healthGateway
            val healthReady = health != null &&
                container.preferences.selectedOrigin() != null &&
                health.enabledTypes().isNotEmpty()
            if (healthReady) {
                val permissions = health!!.permissionStatus()
                if (permissions.backgroundAvailable && permissions.backgroundGranted) {
                    val summary = container.sync(maxPagesPerType = 4)
                    didWork = true
                    needsContinuation = needsContinuation ||
                        summary.completedTypes < health.enabledTypes().size
                }
            }
            if (!didWork) {
                container.preferences.markBackgroundFinished(runId, "not_ready")
                return Result.success()
            }
            if (needsContinuation) {
                SyncScheduler.continueBackfill(applicationContext)
            }
            cloudFailure?.let { throw it }
            container.preferences.markBackgroundFinished(
                runId,
                if (needsContinuation) "backfill_continues" else "success",
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
        } catch (error: XiaomiCloudException.AuthRequired) {
            container.preferences.markBackgroundFinished(
                runId,
                "xiaomi_auth_required",
                "Требуется повторный вход в Xiaomi",
            )
            Result.success()
        } catch (error: XiaomiCloudException.RateLimited) {
            container.preferences.markBackgroundFinished(runId, "xiaomi_rate_limited")
            Result.retry()
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
    private const val XIAOMI_WEEKLY_WORK = "amigo-xiaomi-cloud-weekly-reconcile"
    internal const val INPUT_XIAOMI_REFRESH_DAYS = "xiaomi_refresh_days"
    internal const val INPUT_XIAOMI_BACKFILL_CONTINUATION =
        "xiaomi_backfill_continuation"
    internal val backfillInputData = androidx.work.workDataOf(
        INPUT_XIAOMI_BACKFILL_CONTINUATION to true,
    )
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
        val weekly = PeriodicWorkRequestBuilder<SyncWorker>(7, TimeUnit.DAYS)
            .setConstraints(constraints())
            .setInputData(androidx.work.workDataOf(INPUT_XIAOMI_REFRESH_DAYS to 30L))
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            XIAOMI_WEEKLY_WORK,
            ExistingPeriodicWorkPolicy.UPDATE,
            weekly,
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
            .setInputData(backfillInputData)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            BACKFILL_WORK,
            backfillPolicy,
            request,
        )
    }
}
