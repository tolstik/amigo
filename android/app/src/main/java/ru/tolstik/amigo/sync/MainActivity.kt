package ru.tolstik.amigo.sync

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.content.pm.PackageManager
import android.provider.OpenableColumns
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.core.content.FileProvider
import java.io.File
import java.io.IOException
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.security.MessageDigest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import org.json.JSONObject
import ru.tolstik.amigo.sync.dashboard.DashboardDownloadRequest
import ru.tolstik.amigo.sync.dashboard.DashboardFilePolicy
import ru.tolstik.amigo.sync.dashboard.DashboardUrlPolicy
import ru.tolstik.amigo.sync.dashboard.DashboardWebViewCallbacks
import ru.tolstik.amigo.sync.dashboard.createDashboardWebView
import ru.tolstik.amigo.sync.xiaomi.XiaomiLoginActivity

private enum class AppDestination { DASHBOARD, SYNC }

private data class SelectedDocument(
    val displayName: String?,
    val mimeType: String?,
    val sizeBytes: Long?,
)

private data class AppUpdate(
    val versionCode: Int,
    val versionName: String,
    val sizeBytes: Long,
    val sha256: String,
    val downloadUrl: String,
)

internal fun backgroundResultLabel(value: String?): String = when (value) {
    null -> "—"
    "running" -> "Выполняется"
    "success" -> "Успешно"
    "backfill_continues" -> "История загружается частями"
    "not_paired" -> "Телефон не сопряжён"
    "not_ready" -> "Синхронизация ещё не настроена"
    "health_connect_unavailable" -> "Health Connect недоступен"
    "background_permission_missing" -> "Нет фонового разрешения"
    "permission_revoked" -> "Разрешение отозвано"
    "server_unavailable" -> "Сервер временно недоступен"
    "xiaomi_auth_required" -> "Требуется повторный вход в Xiaomi"
    "xiaomi_rate_limited" -> "Xiaomi временно ограничил запросы"
    "cancelled" -> "Остановлено системой"
    "failed" -> "Ошибка"
    else -> "Неизвестный результат"
}

private class DashboardAuthenticationRequired : IOException()

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()
    private lateinit var dashboardWebView: WebView
    private var selectedDestination by mutableStateOf(AppDestination.DASHBOARD)
    private var dashboardLoading by mutableStateOf(true)
    private var dashboardError by mutableStateOf(false)
    private var dashboardGeneration by mutableIntStateOf(0)
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var pendingDownload: DashboardDownloadRequest? = null
    private var lastDashboardRefreshMs: Long = 0

    private val permissionLauncher = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract(),
    ) { granted -> viewModel.onPermissionsResult(granted) }

    private val xiaomiLoginLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode != Activity.RESULT_OK) return@registerForActivityResult
        val sealed = result.data?.getStringExtra(XiaomiLoginActivity.EXTRA_SEALED_SESSION)
        if (sealed.isNullOrBlank()) {
            Toast.makeText(this, "Xiaomi не вернул защищённую сессию", Toast.LENGTH_LONG).show()
        } else {
            viewModel.enableXiaomiCloud(sealed)
        }
    }

    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val callback = fileChooserCallback
        fileChooserCallback = null
        if (callback == null) return@registerForActivityResult
        val uris = if (result.resultCode == Activity.RESULT_OK) {
            val clip = result.data?.clipData
            when {
                clip != null -> (0 until minOf(clip.itemCount, 25)).map { clip.getItemAt(it).uri }
                result.data?.data != null -> listOf(result.data!!.data!!)
                else -> emptyList()
            }
        } else emptyList()
        if (uris.isEmpty()) {
            callback.onReceiveValue(null)
            return@registerForActivityResult
        }
        val allowed = uris.all { uri ->
            val document = selectedDocument(uri)
            DashboardFilePolicy.isAllowedUpload(document.displayName, document.mimeType, document.sizeBytes)
        }
        if (!allowed) {
            Toast.makeText(this, "Выберите PDF, JPG, PNG или HEIC до 20 МиБ", Toast.LENGTH_LONG).show()
            callback.onReceiveValue(null)
            return@registerForActivityResult
        }
        callback.onReceiveValue(uris.toTypedArray())
    }

    private val saveDocumentLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val request = pendingDownload
        pendingDownload = null
        val uri = result.data?.data?.takeIf { result.resultCode == Activity.RESULT_OK }
        if (request != null && uri != null) downloadTo(request, uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        selectedDestination = savedInstanceState
            ?.getString(STATE_DESTINATION)
            ?.let { runCatching { AppDestination.valueOf(it) }.getOrNull() }
            ?: AppDestination.DASHBOARD
        dashboardWebView = newDashboardWebView()
        val restored = savedInstanceState
            ?.getBundle(STATE_WEB_VIEW)
            ?.let { dashboardWebView.restoreState(it) != null }
            ?: false
        val incomingLink = if (savedInstanceState == null) {
            DashboardUrlPolicy.normalizeAppLink(intent?.dataString)
        } else {
            null
        }
        when {
            incomingLink != null -> {
                selectedDestination = AppDestination.DASHBOARD
                dashboardWebView.loadUrl(incomingLink)
            }
            !restored -> dashboardWebView.loadUrl(DashboardUrlPolicy.ROOT_URL)
        }
        viewModel.setSyncScreenVisible(selectedDestination == AppDestination.SYNC)

        setContent {
            val state by viewModel.state.collectAsStateWithLifecycle()
            MaterialTheme(colorScheme = lightColorScheme()) {
                AmigoApp(state)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val link = DashboardUrlPolicy.normalizeAppLink(intent.dataString)
        if (link == null) {
            showBlockedNavigation()
            return
        }
        selectDestination(AppDestination.DASHBOARD)
        dashboardError = false
        dashboardWebView.loadUrl(link)
    }

    override fun onResume() {
        super.onResume()
        if (selectedDestination == AppDestination.DASHBOARD) {
            dashboardWebView.onResume()
            refreshDashboardIfStale()
        } else {
            viewModel.setSyncScreenVisible(true)
        }
    }

    override fun onPause() {
        viewModel.setSyncScreenVisible(false)
        dashboardWebView.onPause()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString(STATE_DESTINATION, selectedDestination.name)
        val webViewState = Bundle()
        dashboardWebView.saveState(webViewState)
        outState.putBundle(STATE_WEB_VIEW, webViewState)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = null
        (dashboardWebView.parent as? ViewGroup)?.removeView(dashboardWebView)
        dashboardWebView.stopLoading()
        dashboardWebView.destroy()
        super.onDestroy()
    }

    @Composable
    private fun AmigoApp(state: MainUiState) {
        BackHandler {
            when {
                selectedDestination == AppDestination.SYNC -> selectDestination(AppDestination.DASHBOARD)
                dashboardWebView.canGoBack() -> dashboardWebView.goBack()
                else -> finish()
            }
        }
        Scaffold(
            bottomBar = {
                NavigationBar {
                    NavigationBarItem(
                        selected = selectedDestination == AppDestination.DASHBOARD,
                        onClick = { selectDestination(AppDestination.DASHBOARD) },
                        icon = { Icon(Icons.Default.Home, contentDescription = null) },
                        label = { Text("Дашборд") },
                    )
                    NavigationBarItem(
                        selected = selectedDestination == AppDestination.SYNC,
                        onClick = { selectDestination(AppDestination.SYNC) },
                        icon = { Icon(Icons.Default.Sync, contentDescription = null) },
                        label = { Text("Синхронизация") },
                    )
                }
            },
        ) { padding ->
            when (selectedDestination) {
                AppDestination.DASHBOARD -> DashboardScreen(Modifier.padding(padding))
                AppDestination.SYNC -> SyncScreen(
                    state = state,
                    modifier = Modifier.padding(padding),
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
                    onXiaomiLogin = {
                        xiaomiLoginLauncher.launch(Intent(this, XiaomiLoginActivity::class.java))
                    },
                    onXiaomiLogout = viewModel::disableXiaomiCloud,
                    onSync = viewModel::syncNow,
                    onCheckUpdate = ::checkForAppUpdate,
                )
            }
        }
    }

    @Composable
    private fun DashboardScreen(modifier: Modifier) {
        Box(modifier.fillMaxSize()) {
            key(dashboardGeneration) {
                AndroidView(
                    factory = { dashboardWebView },
                    modifier = Modifier.fillMaxSize(),
                )
            }
            if (dashboardLoading && !dashboardError) {
                LinearProgressIndicator(Modifier.fillMaxWidth().align(Alignment.TopCenter))
            }
            if (dashboardError) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    Column(
                        modifier = Modifier.fillMaxSize().padding(28.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Text("Не удалось открыть дашборд", style = MaterialTheme.typography.titleLarge)
                        Text(
                            "Проверьте подключение к интернету и повторите попытку.",
                            modifier = Modifier.padding(top = 8.dp, bottom = 20.dp),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Button(onClick = ::retryDashboard) { Text("Повторить") }
                    }
                }
            }
        }
    }

    private fun newDashboardWebView(): WebView = createDashboardWebView(
        this,
        DashboardWebViewCallbacks(
            onLoadingChanged = { loading ->
                dashboardLoading = loading
                if (!loading) lastDashboardRefreshMs = android.os.SystemClock.elapsedRealtime()
                if (loading) dashboardError = false
            },
            onLoadError = {
                dashboardLoading = false
                dashboardError = true
            },
            onBlockedNavigation = ::showBlockedNavigation,
            onFileChooser = ::showFileChooser,
            onDownload = ::requestDownloadDestination,
            onRendererGone = ::replaceCrashedWebView,
        ),
    )

    private fun selectDestination(destination: AppDestination) {
        if (selectedDestination == destination) return
        selectedDestination = destination
        val syncVisible = destination == AppDestination.SYNC
        viewModel.setSyncScreenVisible(syncVisible)
        if (syncVisible) {
            dashboardWebView.onPause()
        } else {
            dashboardWebView.onResume()
            refreshDashboardIfStale()
        }
    }

    private fun refreshDashboardIfStale() {
        val now = android.os.SystemClock.elapsedRealtime()
        if (lastDashboardRefreshMs > 0 && now - lastDashboardRefreshMs >= 30_000) {
            dashboardWebView.reload()
        }
    }

    private fun retryDashboard() {
        dashboardError = false
        dashboardLoading = true
        val currentUrl = dashboardWebView.url
        if (DashboardUrlPolicy.isAllowedNavigation(currentUrl)) {
            dashboardWebView.reload()
        } else {
            dashboardWebView.loadUrl(DashboardUrlPolicy.ROOT_URL)
        }
    }

    private fun replaceCrashedWebView(crashed: WebView) {
        if (crashed !== dashboardWebView) return
        (crashed.parent as? ViewGroup)?.removeView(crashed)
        crashed.destroy()
        dashboardWebView = newDashboardWebView()
        dashboardGeneration += 1
        dashboardLoading = false
        dashboardError = true
    }

    private fun showBlockedNavigation() {
        Toast.makeText(this, "Amigo заблокировал внешний или небезопасный адрес", Toast.LENGTH_LONG).show()
    }

    private fun showFileChooser(callback: ValueCallback<Array<Uri>>) {
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = callback
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
            putExtra(Intent.EXTRA_MIME_TYPES, DashboardFilePolicy.allowedUploadMimeTypes)
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        fileChooserLauncher.launch(intent)
    }

    private fun selectedDocument(uri: Uri): SelectedDocument {
        var displayName: String? = null
        var sizeBytes: Long? = null
        contentResolver.query(
            uri,
            arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
            null,
            null,
            null,
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (nameIndex >= 0 && !cursor.isNull(nameIndex)) displayName = cursor.getString(nameIndex)
                val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) sizeBytes = cursor.getLong(sizeIndex)
            }
        }
        return SelectedDocument(displayName, contentResolver.getType(uri), sizeBytes)
    }

    private fun requestDownloadDestination(request: DashboardDownloadRequest) {
        if (pendingDownload != null) {
            Toast.makeText(this, "Сначала завершите текущее сохранение", Toast.LENGTH_SHORT).show()
            return
        }
        pendingDownload = request
        val mimeType = request.mimeType
            ?.substringBefore(';')
            ?.takeIf { it in DashboardFilePolicy.allowedUploadMimeTypes || it == "text/csv" }
            ?: "application/octet-stream"
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = mimeType
            putExtra(Intent.EXTRA_TITLE, request.suggestedName)
        }
        saveDocumentLauncher.launch(intent)
    }

    private fun downloadTo(download: DashboardDownloadRequest, destination: Uri) {
        val cookies = CookieManager.getInstance().getCookie(DashboardUrlPolicy.ROOT_URL)
            ?.takeIf(String::isNotBlank)
        if (cookies == null) {
            runCatching { contentResolver.delete(destination, null, null) }
            dashboardWebView.loadUrl(DashboardUrlPolicy.ROOT_URL)
            Toast.makeText(
                this,
                "Сессия завершилась. Войдите снова и повторите скачивание.",
                Toast.LENGTH_LONG,
            ).show()
            return
        }
        lifecycleScope.launch {
            Toast.makeText(this@MainActivity, "Скачивание…", Toast.LENGTH_SHORT).show()
            val result = runCatching {
                withContext(Dispatchers.IO) { downloadToTemporaryFile(download, destination, cookies) }
            }
            result.onSuccess {
                showOpenDocumentDialog(destination, download.mimeType)
            }.onFailure { error ->
                runCatching { contentResolver.delete(destination, null, null) }
                if (error is DashboardAuthenticationRequired) {
                    dashboardWebView.loadUrl(DashboardUrlPolicy.ROOT_URL)
                    Toast.makeText(
                        this@MainActivity,
                        "Сессия завершилась. Войдите снова и повторите скачивание.",
                        Toast.LENGTH_LONG,
                    ).show()
                } else {
                    Toast.makeText(
                        this@MainActivity,
                        "Не удалось сохранить файл",
                        Toast.LENGTH_LONG,
                    ).show()
                }
            }
        }
    }

    private suspend fun downloadToTemporaryFile(
        download: DashboardDownloadRequest,
        destination: Uri,
        cookies: String,
    ) {
        check(DashboardUrlPolicy.isAllowedDownload(download.url))
        val http = (application as AmigoSyncApplication).container.http.newBuilder()
            .followRedirects(false)
            .followSslRedirects(false)
            .build()
        val request = Request.Builder()
            .url(download.url)
            .header("Accept", download.mimeType?.substringBefore(';') ?: "application/octet-stream")
            .header("Cookie", cookies)
            .apply { if (download.userAgent.isNotBlank()) header("User-Agent", download.userAgent) }
            .get()
            .build()
        val temporary = File.createTempFile("amigo-download-", ".tmp", cacheDir)
        temporary.setReadable(false, false)
        temporary.setWritable(false, false)
        temporary.setReadable(true, true)
        temporary.setWritable(true, true)
        try {
            http.newCall(request).execute().use { response ->
                if (response.code == 401) throw DashboardAuthenticationRequired()
                if (!response.isSuccessful || response.isRedirect) {
                    throw IOException("Dashboard download returned HTTP ${response.code}")
                }
                val body = response.body ?: throw IOException("Dashboard download returned no body")
                if (body.contentLength() > DashboardFilePolicy.MAX_DOWNLOAD_BYTES) {
                    throw IOException("Dashboard download is too large")
                }
                body.byteStream().use { input ->
                    temporary.outputStream().buffered().use { output ->
                        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                        var total = 0L
                        while (true) {
                            currentCoroutineContext().ensureActive()
                            val count = input.read(buffer)
                            if (count < 0) break
                            total += count
                            if (total > DashboardFilePolicy.MAX_DOWNLOAD_BYTES) {
                                throw IOException("Dashboard download is too large")
                            }
                            output.write(buffer, 0, count)
                        }
                    }
                }
            }
            contentResolver.openOutputStream(destination, "w")?.use { output ->
                temporary.inputStream().buffered().use { input -> input.copyTo(output) }
            } ?: throw IOException("Cannot open the selected destination")
        } finally {
            temporary.delete()
        }
    }

    private fun showOpenDocumentDialog(uri: Uri, mimeType: String?) {
        AlertDialog.Builder(this)
            .setMessage("Файл сохранён")
            .setNegativeButton("Закрыть", null)
            .setPositiveButton("Открыть") { _, _ ->
                val intent = Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, mimeType?.substringBefore(';') ?: "*/*")
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                try {
                    startActivity(intent)
                } catch (_: ActivityNotFoundException) {
                    Toast.makeText(this, "На телефоне нет приложения для этого файла", Toast.LENGTH_LONG).show()
                }
            }
            .show()
    }

    private fun checkForAppUpdate() {
        val cookies = CookieManager.getInstance().getCookie(DashboardUrlPolicy.ROOT_URL)
            ?.takeIf(String::isNotBlank)
        if (cookies == null) {
            Toast.makeText(this, "Войдите в дашборд перед проверкой обновления", Toast.LENGTH_LONG).show()
            return
        }
        lifecycleScope.launch {
            Toast.makeText(this@MainActivity, "Проверяем обновление…", Toast.LENGTH_SHORT).show()
            val result = runCatching { withContext(Dispatchers.IO) { fetchUpdate(cookies) } }
            result.onSuccess { update ->
                if (update.versionCode <= BuildConfig.VERSION_CODE) {
                    Toast.makeText(this@MainActivity, "Установлена актуальная версия ${BuildConfig.VERSION_NAME}", Toast.LENGTH_LONG).show()
                } else {
                    AlertDialog.Builder(this@MainActivity)
                        .setTitle("Доступна версия ${update.versionName}")
                        .setMessage("APK будет скачан, проверен по SHA-256 и подписи, после чего Android покажет обязательное системное подтверждение установки.")
                        .setNegativeButton("Позже", null)
                        .setPositiveButton("Скачать и установить") { _, _ -> downloadAndInstallUpdate(update, cookies) }
                        .show()
                }
            }.onFailure {
                Toast.makeText(this@MainActivity, "Обновление сейчас недоступно", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun fetchUpdate(cookies: String): AppUpdate {
        val request = Request.Builder()
            .url("${DashboardUrlPolicy.ORIGIN}/amigo/api/v1/app-update")
            .header("Accept", "application/json")
            .header("Cookie", cookies)
            .get()
            .build()
        (application as AmigoSyncApplication).container.http.newCall(request).execute().use { response ->
            if (!response.isSuccessful || response.isRedirect) throw IOException("Update metadata failed")
            val body = response.body?.string() ?: throw IOException("Update metadata is empty")
            val json = JSONObject(body)
            val path = json.getString("download_url")
            check(path == "/amigo/api/v1/app-update/apk")
            val digest = json.getString("sha256").lowercase()
            check(Regex("^[0-9a-f]{64}$").matches(digest))
            return AppUpdate(
                versionCode = json.getInt("version_code"),
                versionName = json.getString("version_name"),
                sizeBytes = json.getLong("size_bytes"),
                sha256 = digest,
                downloadUrl = DashboardUrlPolicy.ORIGIN + path,
            )
        }
    }

    private fun downloadAndInstallUpdate(update: AppUpdate, cookies: String) {
        lifecycleScope.launch {
            Toast.makeText(this@MainActivity, "Скачиваем обновление…", Toast.LENGTH_LONG).show()
            val result = runCatching { withContext(Dispatchers.IO) { downloadVerifiedApk(update, cookies) } }
            result.onSuccess(::openSystemInstaller).onFailure {
                Toast.makeText(this@MainActivity, "APK не прошёл проверку или не загрузился", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun downloadVerifiedApk(update: AppUpdate, cookies: String): File {
        check(update.downloadUrl == "${DashboardUrlPolicy.ORIGIN}/amigo/api/v1/app-update/apk")
        check(update.sizeBytes in 1..150L * 1024 * 1024)
        val directory = File(cacheDir, "updates").apply { mkdirs() }
        val target = File(directory, "amigo-sync-${update.versionCode}.apk")
        val digest = MessageDigest.getInstance("SHA-256")
        val request = Request.Builder()
            .url(update.downloadUrl)
            .header("Accept", "application/vnd.android.package-archive")
            .header("Cookie", cookies)
            .get()
            .build()
        try {
            (application as AmigoSyncApplication).container.http.newCall(request).execute().use { response ->
                if (!response.isSuccessful || response.isRedirect) throw IOException("APK download failed")
                val body = response.body ?: throw IOException("APK body is empty")
                if (body.contentLength() !in -1..update.sizeBytes) throw IOException("APK length mismatch")
                var total = 0L
                body.byteStream().use { input ->
                    target.outputStream().buffered().use { output ->
                        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            total += count
                            if (total > update.sizeBytes) throw IOException("APK too large")
                            digest.update(buffer, 0, count)
                            output.write(buffer, 0, count)
                        }
                    }
                }
                if (total != update.sizeBytes) throw IOException("APK size mismatch")
            }
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            if (actual != update.sha256 || !hasCurrentAppSigner(target, update.versionCode)) {
                throw IOException("APK verification failed")
            }
            return target
        } catch (error: Exception) {
            target.delete()
            throw error
        }
    }

    @Suppress("DEPRECATION")
    private fun hasCurrentAppSigner(apk: File, expectedVersionCode: Int): Boolean {
        val flags = PackageManager.GET_SIGNING_CERTIFICATES
        val archive = packageManager.getPackageArchiveInfo(apk.absolutePath, flags) ?: return false
        if (
            archive.packageName != packageName ||
            archive.longVersionCode != expectedVersionCode.toLong() ||
            archive.longVersionCode <= BuildConfig.VERSION_CODE
        ) return false
        val installed = packageManager.getPackageInfo(packageName, flags)
        fun digests(info: android.content.pm.PackageInfo): Set<String> =
            info.signingInfo?.apkContentsSigners.orEmpty().map { signature ->
                MessageDigest.getInstance("SHA-256").digest(signature.toByteArray()).joinToString("") { "%02x".format(it) }
            }.toSet()
        return digests(archive).isNotEmpty() && digests(archive) == digests(installed)
    }

    private fun openSystemInstaller(apk: File) {
        val uri = FileProvider.getUriForFile(this, "$packageName.updates", apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(intent)
    }

    companion object {
        private const val STATE_DESTINATION = "amigo.destination"
        private const val STATE_WEB_VIEW = "amigo.web_view"
    }
}

@Composable
private fun SyncScreen(
    state: MainUiState,
    modifier: Modifier = Modifier,
    onRequestPermissions: () -> Unit,
    onDiscoverOrigins: () -> Unit,
    onSelectOrigin: (String) -> Unit,
    onServerUrlChange: (String) -> Unit,
    onDeviceLabelChange: (String) -> Unit,
    onRegister: () -> Unit,
    onCheckPairing: () -> Unit,
    onResetPairing: () -> Unit,
    onXiaomiLogin: () -> Unit,
    onXiaomiLogout: () -> Unit,
    onSync: () -> Unit,
    onCheckUpdate: () -> Unit,
) {
    val local = state.local
    LazyColumn(
        modifier = modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Spacer(Modifier.height(8.dp))
            Text("Синхронизация", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Text(
                "Mi Fitness → Xiaomi Cloud → Amigo",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        state.notice?.let { notice ->
            item { StatusCard("Сообщение", notice) }
        }
        item {
            SectionCard("1. Health Connect (резервный источник)") {
                Text(healthStatusText(state.healthSdkStatus))
                state.permissions?.let { permissions ->
                    Text("Разрешения: ${permissions.granted.intersect(permissions.requested).size}/${permissions.requested.size}")
                    if (!permissions.hasMetricReadPermission) {
                        Text(
                            "Нет доступа ни к одному показателю здоровья",
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
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
                    enabled = !state.busy && state.permissions?.hasMetricReadPermission == true,
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
                        enabled = !state.busy,
                    ) { Text("Зарегистрировать телефон") }
                } else {
                    if (registration.pairingCode.isNotBlank()) {
                        Text("Код: ${registration.pairingCode}", style = MaterialTheme.typography.headlineSmall)
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
            val cloud = state.xiaomi
            SectionCard("3. Xiaomi Cloud напрямую") {
                Text("Пароль вводится только на странице Xiaomi и не передаётся Amigo.")
                Text("Статус: ${xiaomiStatusLabel(cloud?.status)}")
                Text("Регион: ${cloud?.region ?: "—"}")
                Text("Основной источник: ${if (cloud?.active == true) "да" else "нет, идёт проверка"}")
                Text("История: ${cloud?.completedTypes ?: 0}/10 типов")
                Text("Последняя cloud-синхронизация: ${formatInstant(cloud?.lastSync)}")
                Text("Cloud-данные актуальны на: ${formatInstant(cloud?.dataAsOf)}")
                cloud?.lastErrorCode?.let {
                    Text("Код ошибки: $it", color = MaterialTheme.colorScheme.error)
                }
                if (cloud?.enabled == true || cloud?.hasCredentials == true) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = onXiaomiLogin,
                            enabled = !state.busy && local?.registration?.status == "approved",
                        ) { Text("Войти заново") }
                        OutlinedButton(onClick = onXiaomiLogout, enabled = !state.busy) {
                            Text("Отключить")
                        }
                    }
                } else {
                    Button(
                        onClick = onXiaomiLogin,
                        enabled = !state.busy && local?.registration?.status == "approved",
                    ) { Text("Войти в Xiaomi") }
                }
            }
        }
        item {
            SectionCard("4. Синхронизация") {
                Text("Фоновая проверка выполняется примерно раз в час.")
                Text("История: ${local?.completedTypes ?: 0}/11 типов")
                Text("Последняя отправка: ${formatInstant(local?.lastSync)}")
                Text("Данные актуальны на: ${formatInstant(local?.dataAsOf)}")
                Text("Последний фоновый запуск: ${formatInstant(local?.backgroundLastStarted)}")
                Text("Фоновый результат: ${backgroundResultLabel(local?.backgroundResult)}")
                Text("Фоновый запуск завершён: ${formatInstant(local?.backgroundLastFinished)}")
                local?.lastError?.let { Text("Ошибка: $it", color = MaterialTheme.colorScheme.error) }
                Button(
                    onClick = onSync,
                    enabled = !state.busy && local?.registration?.status == "approved" &&
                        (state.xiaomi?.enabled == true || local?.selectedOrigin != null),
                ) { Text("Синхронизировать сейчас") }
                if (state.busy) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(modifier = Modifier.height(24.dp))
                        Text(" Ручная синхронизация…")
                    }
                }
            }
        }
        item {
            SectionCard("5. Обновление приложения") {
                Text("Текущая версия: ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
                OutlinedButton(onClick = onCheckUpdate, enabled = !state.busy) { Text("Проверить обновление") }
            }
        }
        item {
            HorizontalDivider()
            Text(
                "Из Xiaomi Cloud импортируются только активность, сон, пульс, SpO₂ и VO₂ max. " +
                    "Вес, состав тела, давление, GPS и маршруты не запрашиваются.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 24.dp),
            )
        }
    }
}

private fun xiaomiStatusLabel(value: String?): String = when (value) {
    "pending" -> "проверяем данные"
    "success" -> "работает"
    "auth_required" -> "требуется повторный вход"
    "rate_limited" -> "Xiaomi временно ограничил запросы"
    "network_error" -> "сеть временно недоступна"
    "disabled", null -> "не подключён"
    else -> "неизвестен"
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
