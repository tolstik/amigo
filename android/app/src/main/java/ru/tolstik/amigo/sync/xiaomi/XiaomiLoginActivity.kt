package ru.tolstik.amigo.sync.xiaomi

import android.app.Activity
import android.net.Uri
import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import java.io.ByteArrayInputStream
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient

class XiaomiLoginActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var progress: ProgressBar
    private lateinit var message: TextView
    private var completing = false

    override fun onCreate(savedInstanceState: Bundle?) {
        WebView.setDataDirectorySuffix("xiaomi_auth")
        super.onCreate(savedInstanceState)
        title = "Вход в Xiaomi"
        val root = FrameLayout(this)
        webView = WebView(this).apply {
            isFocusable = true
            isFocusableInTouchMode = true
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_YES
            setOnTouchListener { view, event ->
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> view.requestFocus()
                    MotionEvent.ACTION_UP -> view.post {
                        getSystemService(InputMethodManager::class.java)
                            ?.showSoftInput(view, InputMethodManager.SHOW_IMPLICIT)
                    }
                }
                false
            }
        }
        progress = ProgressBar(this).apply { isIndeterminate = true }
        message = TextView(this).apply {
            textSize = 16f
            setPadding(32, 32, 32, 32)
            visibility = View.GONE
        }
        root.addView(
            webView,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            ),
        )
        root.addView(
            progress,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                android.view.Gravity.CENTER,
            ),
        )
        root.addView(
            message,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                android.view.Gravity.CENTER,
            ),
        )
        setContentView(root)
        configureWebView()
        val cookies = CookieManager.getInstance()
        cookies.setAcceptCookie(true)
        cookies.setAcceptThirdPartyCookies(webView, false)
        val restored = savedInstanceState?.let { webView.restoreState(it) } != null
        if (restored) {
            webView.requestFocus()
        } else {
            cookies.removeAllCookies {
                cookies.flush()
                webView.loadUrl(XiaomiPassportClient.LOGIN_URL)
            }
        }
    }

    @Suppress("SetJavaScriptEnabled")
    private fun configureWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            allowFileAccess = false
            allowContentAccess = false
            setGeolocationEnabled(false)
            mediaPlaybackRequiresUserGesture = true
            javaScriptCanOpenWindowsAutomatically = false
            setSupportMultipleWindows(false)
            safeBrowsingEnabled = true
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                progress.visibility = if (newProgress < 100 && !completing) View.VISIBLE else View.GONE
            }
        }
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val target = request.url.toString()
                if (!request.isForMainFrame) return false
                if (!XiaomiPassportClient.isAllowedLoginNavigation(target)) {
                    fail("Xiaomi перенаправил вход на недопустимый адрес")
                    return true
                }
                return false
            }

            override fun shouldInterceptRequest(
                view: WebView,
                request: WebResourceRequest,
            ): WebResourceResponse? {
                val uri = request.url
                if (uri.scheme == "https" && XiaomiPassportClient.isAllowedLoginResourceHost(uri.host)) {
                    return null
                }
                return WebResourceResponse(
                    "text/plain",
                    "UTF-8",
                    403,
                    "Blocked",
                    mapOf("Cache-Control" to "no-store"),
                    ByteArrayInputStream(ByteArray(0)),
                )
            }

            override fun onReceivedSslError(
                view: WebView?,
                handler: SslErrorHandler,
                error: android.net.http.SslError?,
            ) {
                handler.cancel()
                fail("Не удалось проверить TLS-сертификат Xiaomi")
            }

            override fun onPageFinished(view: WebView, url: String) {
                if (!completing && XiaomiPassportClient.isStsCallback(url)) complete(url)
            }
        }
    }

    private fun complete(callbackUrl: String) {
        completing = true
        webView.stopLoading()
        webView.visibility = View.GONE
        progress.visibility = View.VISIBLE
        message.visibility = View.VISIBLE
        message.text = "Завершаем защищённый вход…"
        val cookieManager = CookieManager.getInstance()
        val accountCookies = cookieManager.getCookie("https://account.xiaomi.com/")
        val stsCookies = cookieManager.getCookie("https://sts-hlth.io.mi.com/")
        lifecycleScope.launch {
            runCatching {
                XiaomiPassportClient(OkHttpClient()).completeBrowserLogin(
                    callbackUrl,
                    accountCookies,
                    stsCookies,
                )
            }.onSuccess { credentials ->
                val sealed = XiaomiCredentialStore(this@XiaomiLoginActivity).seal(credentials)
                clearBrowserSession {
                    setResult(
                        Activity.RESULT_OK,
                        android.content.Intent().putExtra(EXTRA_SEALED_SESSION, sealed),
                    )
                    finish()
                }
            }.onFailure {
                fail("Не удалось завершить вход Xiaomi. Повторите попытку.")
            }
        }
    }

    private fun clearBrowserSession(onComplete: () -> Unit) {
        WebStorage.getInstance().deleteAllData()
        webView.clearFormData()
        webView.clearHistory()
        webView.clearCache(true)
        CookieManager.getInstance().removeAllCookies {
            CookieManager.getInstance().flush()
            onComplete()
        }
    }

    private fun fail(text: String) {
        completing = false
        progress.visibility = View.GONE
        webView.visibility = View.GONE
        message.visibility = View.VISIBLE
        message.text = text
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
        webView.post {
            if (webView.visibility == View.VISIBLE) {
                webView.requestFocus()
                if (webView.hasFocus()) {
                    getSystemService(InputMethodManager::class.java)
                        ?.showSoftInput(webView, InputMethodManager.SHOW_IMPLICIT)
                }
            }
        }
    }

    override fun onPause() {
        CookieManager.getInstance().flush()
        webView.onPause()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        webView.saveState(outState)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        if (isFinishing && !completing) {
            CookieManager.getInstance().removeAllCookies(null)
            CookieManager.getInstance().flush()
            WebStorage.getInstance().deleteAllData()
            webView.clearFormData()
            webView.clearHistory()
            webView.clearCache(true)
        }
        webView.stopLoading()
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_SEALED_SESSION = "sealed_xiaomi_session"
    }
}
