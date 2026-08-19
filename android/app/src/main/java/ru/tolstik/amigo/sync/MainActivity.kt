package ru.tolstik.amigo.sync

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()
    private val permissionLauncher = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract(),
    ) { granted -> viewModel.onPermissionsResult(granted) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val state by viewModel.state.collectAsStateWithLifecycle()
            MaterialTheme(colorScheme = lightColorScheme()) {
                AmigoSyncScreen(
                    state = state,
                    onRequestPermissions = {
                        val permissions = viewModel.permissionsToRequest()
                        if (permissions.isNotEmpty()) permissionLauncher.launch(permissions)
                    },
                    onDiscoverOrigins = viewModel::discoverOrigins,
                    onSelectOrigin = viewModel::selectOrigin,
                    onServerUrlChange = viewModel::setServerUrl,
                    onDeviceLabelChange = viewModel::setDeviceLabel,
                    onRegister = viewModel::registerDevice,
                    onCheckPairing = viewModel::checkPairing,
                    onResetPairing = viewModel::resetPairing,
                    onSync = viewModel::syncNow,
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        viewModel.refresh()
    }
}

@Composable
private fun AmigoSyncScreen(
    state: MainUiState,
    onRequestPermissions: () -> Unit,
    onDiscoverOrigins: () -> Unit,
    onSelectOrigin: (String) -> Unit,
    onServerUrlChange: (String) -> Unit,
    onDeviceLabelChange: (String) -> Unit,
    onRegister: () -> Unit,
    onCheckPairing: () -> Unit,
    onResetPairing: () -> Unit,
    onSync: () -> Unit,
) {
    val local = state.local
    Scaffold { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Spacer(Modifier.height(8.dp))
                Text("Amigo Sync", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Text(
                    "Mi Fitness → Health Connect → Amigo",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            state.notice?.let { notice ->
                item { StatusCard("Сообщение", notice) }
            }
            item {
                SectionCard("1. Health Connect") {
                    Text(healthStatusText(state.healthSdkStatus))
                    state.permissions?.let { permissions ->
                        Text("Разрешения: ${permissions.granted.intersect(permissions.requested).size}/${permissions.requested.size}")
                        Text(
                            when {
                                !permissions.historyAvailable -> "Расширенный доступ к истории не поддерживается"
                                permissions.historyGranted -> "Вся доступная история разрешена"
                                else -> "История старше 30 дней не разрешена"
                            },
                        )
                        Text(
                            when {
                                !permissions.backgroundAvailable -> "Фоновое чтение не поддерживается"
                                permissions.backgroundGranted -> "Фоновое чтение разрешено"
                                else -> "Фоновое чтение не разрешено"
                            },
                        )
                    }
                    Button(
                        onClick = onRequestPermissions,
                        enabled = !state.busy && state.healthSdkStatus == HealthConnectClient.SDK_AVAILABLE,
                    ) { Text("Выдать доступ") }
                    OutlinedButton(
                        onClick = onDiscoverOrigins,
                        enabled = !state.busy && state.permissions?.granted?.isNotEmpty() == true,
                    ) { Text("Найти источники") }
                }
            }
            if (state.origins.isNotEmpty()) {
                item { Text("Источник данных", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold) }
                items(state.origins, key = OriginItem::packageName) { origin ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .selectable(
                                selected = local?.selectedOrigin == origin.packageName,
                                enabled = !state.busy,
                                role = Role.RadioButton,
                                onClick = { onSelectOrigin(origin.packageName) },
                            )
                            .padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(
                            selected = local?.selectedOrigin == origin.packageName,
                            onClick = null,
                        )
                        Column(Modifier.padding(start = 8.dp)) {
                            Text(origin.label, fontWeight = FontWeight.Medium)
                            Text(origin.packageName, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
            item {
                SectionCard("2. Сопряжение с сервером") {
                    OutlinedTextField(
                        value = local?.serverUrl.orEmpty(),
                        onValueChange = onServerUrlChange,
                        label = { Text("Адрес сервера") },
                        enabled = !state.busy && local?.registration == null,
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = local?.deviceLabel.orEmpty(),
                        onValueChange = onDeviceLabelChange,
                        label = { Text("Название телефона") },
                        enabled = !state.busy && local?.registration == null,
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    val registration = local?.registration
                    if (registration == null) {
                        Button(
                            onClick = onRegister,
                            enabled = !state.busy && local?.selectedOrigin != null,
                        ) { Text("Зарегистрировать телефон") }
                    } else {
                        if (registration.pairingCode.isNotBlank()) {
                            Text(
                                "Код: ${registration.pairingCode}",
                                style = MaterialTheme.typography.headlineSmall,
                            )
                        }
                        Text("Статус: ${registration.status}")
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = onCheckPairing, enabled = !state.busy) { Text("Проверить") }
                            OutlinedButton(onClick = onResetPairing, enabled = !state.busy) { Text("Сбросить") }
                        }
                    }
                }
            }
            item {
                SectionCard("3. Синхронизация") {
                    Text("Фоновая проверка выполняется примерно раз в час.")
                    Text("История: ${local?.completedTypes ?: 0}/11 типов")
                    Text("Последняя отправка: ${formatInstant(local?.lastSync)}")
                    Text("Данные актуальны на: ${formatInstant(local?.dataAsOf)}")
                    local?.lastError?.let { Text("Ошибка: $it", color = MaterialTheme.colorScheme.error) }
                    Button(
                        onClick = onSync,
                        enabled = !state.busy && local?.registration?.status == "approved" && local?.selectedOrigin != null,
                    ) { Text("Синхронизировать сейчас") }
                    if (state.busy) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(modifier = Modifier.height(24.dp))
                            Text(" Выполняется…")
                        }
                    }
                }
            }
            item {
                HorizontalDivider()
                Text(
                    "Приложение только читает выбранные показатели. Вес, давление, GPS и маршруты не запрашиваются.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 24.dp),
                )
            }
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            content()
        }
    }
}

@Composable
private fun StatusCard(title: String, value: String) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp)) {
            Text(title, style = MaterialTheme.typography.labelMedium)
            Text(value)
        }
    }
}

private fun healthStatusText(status: Int): String = when (status) {
    HealthConnectClient.SDK_AVAILABLE -> "Health Connect доступен"
    HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> "Нужно установить или обновить Health Connect"
    else -> "Health Connect недоступен на этом телефоне"
}

private fun formatInstant(value: Instant?): String {
    if (value == null) return "—"
    return DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm")
        .withZone(ZoneId.systemDefault())
        .format(value)
}
