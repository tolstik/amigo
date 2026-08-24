package ru.tolstik.amigo.sync.dashboard

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.net.http.SslError
import android.webkit.CookieManager
import android.webkit.SafeBrowsingResponse
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.ByteArrayInputStream
import ru.tolstik.amigo.sync.BuildConfig

data class DashboardWebViewCallbacks(
    val onLoadingChanged: (Boolean) -> Unit,
    val onLoadError: () -> Unit,
    val onBlockedNavigation: () -> Unit,
    val onFileChooser: (ValueCallback<Array<android.net.Uri>>) -> Unit,
    val onDownload: (DashboardDownloadRequest) -> Unit,
    val onRendererGone: (WebView) -> Unit,
)

@SuppressLint("SetJavaScriptEnabled")
fun createDashboardWebView(
    context: Context,
    callbacks: DashboardWebViewCallbacks,
): WebView {
    WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
    return WebView(context).apply webView@{
        importantForAutofill = WebView.IMPORTANT_FOR_AUTOFILL_YES
        settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = true
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            safeBrowsingEnabled = true
            mediaPlaybackRequiresUserGesture = true
            cacheMode = WebSettings.LOAD_NO_CACHE
            builtInZoomControls = false
            displayZoomControls = false
            setGeolocationEnabled(false)
        }
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(this@webView, false)
        }
        webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                callbacks.onLoadingChanged(newProgress < 100)
            }

            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<android.net.Uri>>?,
                fileChooserParams: FileChooserParams?,
            ): Boolean {
                if (filePathCallback == null) return false
                callbacks.onFileChooser(filePathCallback)
                return true
            }
        }
        webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(
                view: WebView?,
                request: WebResourceRequest?,
            ): WebResourceResponse? {
                if (request == null || DashboardUrlPolicy.isCanonicalOrigin(request.url.toString())) {
                    return null
                }
                return WebResourceResponse(
                    "text/plain",
                    "utf-8",
                    403,
                    "Blocked",
                    mapOf("Cache-Control" to "no-store"),
                    ByteArrayInputStream(ByteArray(0)),
                )
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                if (request == null || !request.isForMainFrame) return false
                val url = request.url.toString()
                if (DashboardUrlPolicy.isInternalErrorPage(url)) {
                    callbacks.onLoadError()
                    return true
                }
                if (
                    DashboardUrlPolicy.isAllowedNavigation(url) ||
                    DashboardUrlPolicy.isAllowedDownload(url, request.method)
                ) {
                    return false
                }
                callbacks.onBlockedNavigation()
                return true
            }

            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                if (DashboardUrlPolicy.isAllowedNavigation(url)) {
                    callbacks.onLoadingChanged(true)
                }
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                if (DashboardUrlPolicy.isAllowedNavigation(url)) {
                    CookieManager.getInstance().flush()
                    callbacks.onLoadingChanged(false)
                }
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (request?.isForMainFrame == true) callbacks.onLoadError()
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?,
            ) {
                if (request?.isForMainFrame == true && (errorResponse?.statusCode ?: 0) >= 400) {
                    callbacks.onLoadError()
                }
            }

            override fun onReceivedSslError(view: WebView?, handler: SslErrorHandler?, error: SslError?) {
                handler?.cancel()
                callbacks.onLoadError()
            }

            override fun onSafeBrowsingHit(
                view: WebView?,
                request: WebResourceRequest?,
                threatType: Int,
                callback: SafeBrowsingResponse?,
            ) {
                callback?.backToSafety(true)
                callbacks.onLoadError()
            }

            override fun onRenderProcessGone(
                view: WebView?,
                detail: android.webkit.RenderProcessGoneDetail?,
            ): Boolean {
                view?.let(callbacks.onRendererGone)
                return true
            }
        }
        setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            if (!DashboardUrlPolicy.isAllowedDownload(url)) {
                callbacks.onBlockedNavigation()
                return@setDownloadListener
            }
            callbacks.onDownload(
                DashboardDownloadRequest(
                    url = url,
                    userAgent = userAgent.orEmpty(),
                    contentDisposition = contentDisposition,
                    mimeType = mimeType,
                    suggestedName = DashboardFilePolicy.suggestedDownloadName(
                        contentDisposition,
                        mimeType,
                        url,
                    ),
                ),
            )
        }
    }
}
