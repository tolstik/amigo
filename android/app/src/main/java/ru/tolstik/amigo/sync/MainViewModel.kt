package ru.tolstik.amigo.sync

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import ru.tolstik.amigo.sync.data.DeviceRegistration
import ru.tolstik.amigo.sync.data.LocalStatus
import ru.tolstik.amigo.sync.health.HealthPermissionStatus
import ru.tolstik.amigo.sync.sync.userFacingSyncError

data class OriginItem(val packageName: String, val label: String)

data class MainUiState(
    val healthSdkStatus: Int,
    val permissions: HealthPermissionStatus? = null,
    val local: LocalStatus? = null,
    val origins: List<OriginItem> = emptyList(),
    val busy: Boolean = false,
    val notice: String? = null,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val container = (application as AmigoSyncApplication).container
    private val preferences = container.preferences
    private val _state = MutableStateFlow(
        MainUiState(healthSdkStatus = HealthConnectGatewayStatus.current(application)),
    )
    val state: StateFlow<MainUiState> = _state.asStateFlow()
    private var refreshJob: Job? = null

    fun setSyncScreenVisible(visible: Boolean) {
        if (!visible) {
            refreshJob?.cancel()
            refreshJob = null
            return
        }
        if (refreshJob?.isActive == true) return
        refreshJob = viewModelScope.launch {
            while (isActive) {
                refreshInternal()
                delay(5_000)
            }
        }
    }

    fun permissionsToRequest(): Set<String> =
        container.healthGateway?.permissionsToRequest().orEmpty()

    fun refresh() {
        viewModelScope.launch { refreshInternal() }
    }

    fun onPermissionsResult(granted: Set<String>) {
        launchBusy {
            if (granted.isNotEmpty()) {
                preferences.markHealthPermissionsObserved()
                container.resetSnapshotsAfterPermissionsChange()
            }
            showNotice(if (granted.isEmpty()) "Доступ не предоставлен" else "Разрешения обновлены")
        }
    }

    fun setServerUrl(value: String) {
        preferences.setServerUrl(value)
        refresh()
    }

    fun setDeviceLabel(value: String) {
        preferences.setDeviceLabel(value)
        refresh()
    }

    fun discoverOrigins() = launchBusy {
        val gateway = container.healthGateway ?: error("Health Connect недоступен")
        val found = gateway.discoverOrigins()
        preferences.setDiscoveredOrigins(found)
        showNotice(
            if (found.isEmpty()) "За последние 30 дней источники не найдены"
            else "Найдено источников: ${found.size}",
        )
    }

    fun selectOrigin(packageName: String) = launchBusy {
        preferences.selectOrigin(packageName)
        showNotice("Источник выбран")
    }

    fun registerDevice() = launchBusy {
        val current = preferences.status()
        require(current.selectedOrigin != null) { "Сначала выберите источник Mi Fitness" }
        val enabledTypes = container.healthGateway?.enabledTypes().orEmpty()
        check(enabledTypes.isNotEmpty()) {
            "Сначала разрешите чтение хотя бы одного показателя Health Connect"
        }
        val response = container.ingestApi.register(current.serverUrl, current.deviceLabel)
        preferences.saveRegistration(
            DeviceRegistration(
                serverUrl = current.serverUrl,
                deviceId = response.deviceId,
                pairingCode = response.pairingCode.orEmpty(),
                status = response.status,
            ),
        )
        showNotice(
            if (response.status == "approved") "Устройство уже подтверждено"
            else "Устройство зарегистрировано. Подтвердите код на сервере.",
        )
    }

    fun checkPairing() = launchBusy {
        val current = preferences.registration() ?: error("Устройство ещё не зарегистрировано")
        val response = container.ingestApi.status(current.serverUrl, current.deviceId)
        preferences.updatePairingStatus(response.status, response.pairingCode)
        showNotice(
            if (response.status == "approved") "Сопряжение подтверждено"
            else "Текущий статус: ${response.status}",
        )
    }

    fun resetPairing() = launchBusy {
        container.resetPairing()
        showNotice("Сопряжение сброшено, ключ устройства заменён")
    }

    fun syncNow() = launchBusy {
        val registration = preferences.registration()
        check(registration?.status == "approved") { "Сначала подтвердите сопряжение" }
        val summary = container.sync(maxPagesPerType = 12)
        showNotice(
            "Отправлено пакетов: ${summary.uploadedBatches}; история: " +
                "${summary.completedTypes}/${container.healthGateway?.enabledTypes()?.size ?: 0}",
        )
    }

    private fun launchBusy(block: suspend () -> Unit) {
        if (_state.value.busy) return
        viewModelScope.launch {
            _state.value = _state.value.copy(busy = true, notice = null)
            try {
                block()
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                showNotice(userFacingSyncError(error))
            } finally {
                _state.value = _state.value.copy(busy = false)
                if (currentCoroutineContext().isActive) refreshInternal()
            }
        }
    }

    private suspend fun refreshInternal() {
        val local = preferences.status()
        val permissions = runCatching { container.healthGateway?.permissionStatus() }.getOrNull()
        if (permissions?.granted?.isNotEmpty() == true) preferences.markHealthPermissionsObserved()
        val origins = local.discoveredOrigins.sorted().map { packageName ->
            OriginItem(packageName, applicationLabel(packageName))
        }
        _state.value = _state.value.copy(
            healthSdkStatus = HealthConnectGatewayStatus.current(getApplication()),
            permissions = permissions,
            local = local,
            origins = origins,
        )
    }

    private fun applicationLabel(packageName: String): String {
        val manager = getApplication<Application>().packageManager
        return try {
            @Suppress("DEPRECATION")
            val info = manager.getApplicationInfo(packageName, 0)
            manager.getApplicationLabel(info).toString()
        } catch (_: Exception) {
            packageName
        }
    }

    private fun showNotice(value: String) {
        _state.value = _state.value.copy(notice = value)
    }
}

private object HealthConnectGatewayStatus {
    fun current(application: Application): Int =
        ru.tolstik.amigo.sync.health.HealthConnectGateway.sdkStatus(application)
}
