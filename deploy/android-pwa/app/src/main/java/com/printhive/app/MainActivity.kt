package com.printhive.app

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.webkit.CookieManager
import android.webkit.DownloadListener
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.graphics.Color
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private var fileUploadCallback: ValueCallback<Array<Uri>>? = null
    private var cachedTopInsetDp: Int = 0
    private var cachedBottomInsetDp: Int = 0

    private val filePickerLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val uris = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        fileUploadCallback?.onReceiveValue(uris)
        fileUploadCallback = null
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            PrintHiveLiveService.startService(this)
        }
    }

    private val targetUrl = "https://192.168.1.250/"

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Ensure status bar and navigation bar are transparent with light icons
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            window.isStatusBarContrastEnforced = false
            window.isNavigationBarContrastEnforced = false
        }
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }

        swipeRefresh = findViewById(R.id.swipeRefresh)
        webView = findViewById(R.id.webView)

        setupInsets()
        setupSwipeRefresh()
        setupWebView()
        setupBackNavigation()
        setupLiveNotifications()

        if (savedInstanceState == null) {
            webView.loadUrl(targetUrl)
        } else {
            webView.restoreState(savedInstanceState)
        }
    }

    private fun setupLiveNotifications() {
        LiveNotificationManager.createNotificationChannels(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED) {
                PrintHiveLiveService.startService(this)
            } else {
                notificationPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
            }
        } else {
            PrintHiveLiveService.startService(this)
        }
    }

    private fun setupInsets() {
        // Draw edge-to-edge behind system bars without clipping or artificial letterboxing.
        // We capture status and navigation insets and dynamically provide them as CSS variables.
        ViewCompat.setOnApplyWindowInsetsListener(swipeRefresh) { _, windowInsets ->
            val statusInsets = windowInsets.getInsets(
                WindowInsetsCompat.Type.statusBars() or WindowInsetsCompat.Type.displayCutout()
            )
            val navInsets = windowInsets.getInsets(
                WindowInsetsCompat.Type.navigationBars()
            )
            val density = resources.displayMetrics.density
            val topDp = if (density > 0) (statusInsets.top / density).toInt() else 0
            val bottomDp = if (density > 0) (navInsets.bottom / density).toInt() else 0

            cachedTopInsetDp = topDp
            cachedBottomInsetDp = bottomDp

            swipeRefresh.setProgressViewOffset(
                false,
                statusInsets.top,
                statusInsets.top + (64 * density).toInt()
            )

            injectSafeAreas(webView)
            windowInsets
        }
    }

    private fun injectSafeAreas(view: WebView? = webView) {
        val script = """
            (function() {
                document.documentElement.style.setProperty('--safe-area-top', '${cachedTopInsetDp}px');
                document.documentElement.style.setProperty('--safe-area-bottom', '${cachedBottomInsetDp}px');
            })();
        """.trimIndent()
        view?.evaluateJavascript(script, null)
    }

    private fun setupSwipeRefresh() {
        swipeRefresh.setColorSchemeResources(R.color.primary)
        swipeRefresh.setProgressBackgroundColorSchemeResource(R.color.background)
        swipeRefresh.setOnRefreshListener {
            webView.reload()
            PrintHiveLiveService.triggerImmediatePoll(this)
        }
    }

    private fun injectStandaloneStyles(view: WebView?) {
        injectSafeAreas(view)
        view?.evaluateJavascript("""
            (function() {
                window.isPrintHiveApp = true;
                if (!document.getElementById('printhive-app-injected-style')) {
                    const style = document.createElement('style');
                    style.id = 'printhive-app-injected-style';
                    style.innerHTML = `
                        button[aria-label="Install App Guide"],
                        button[title*="Install"],
                        button[title*="install"],
                        .pwa-install-button {
                            display: none !important;
                        }
                    `;
                    document.head.appendChild(style);
                }

                function syncAuth() {
                    try {
                        let token = sessionStorage.getItem('auth_token') || localStorage.getItem('auth_token') || '';
                        if (!token && window.PrintHiveBridge) {
                            const bridgeToken = window.PrintHiveBridge.getAuthToken();
                            if (bridgeToken) {
                                localStorage.setItem('auth_token', bridgeToken);
                                window.location.reload();
                                return;
                            }
                        }
                        if (window.PrintHiveBridge && token) {
                            window.PrintHiveBridge.setAuthToken(token);
                        }
                    } catch(e) {}
                }
                syncAuth();
                if (!window.__printhive_auth_sync_timer) {
                    window.__printhive_auth_sync_timer = setInterval(syncAuth, 10000);
                }
            })();
        """.trimIndent(), null)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.allowFileAccess = true
        settings.allowContentAccess = true
        settings.loadWithOverviewMode = true
        settings.useWideViewPort = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.cacheMode = WebSettings.LOAD_DEFAULT

        // Append custom user-agent token so the web app detects native app environment
        val defaultUa = settings.userAgentString
        settings.userAgentString = "$defaultUa PrintHiveApp/1.0"

        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        webView.addJavascriptInterface(object {
            @android.webkit.JavascriptInterface
            fun pollNow() {
                PrintHiveLiveService.triggerImmediatePoll(this@MainActivity)
            }

            @android.webkit.JavascriptInterface
            fun getAuthToken(): String {
                return AuthStore.getToken(this@MainActivity) ?: ""
            }

            @android.webkit.JavascriptInterface
            fun setAuthToken(token: String?) {
                val changed = AuthStore.setToken(this@MainActivity, token)
                if (changed) {
                    PrintHiveLiveService.triggerImmediatePoll(this@MainActivity)
                }
            }
        }, "PrintHiveBridge")

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url ?: return false
                val host = url.host ?: ""
                if (host == "192.168.1.250" || host == "printhive.local.home" || host.endsWith(".local.home") || host == "localhost") {
                    return false
                }
                return try {
                    val intent = Intent(Intent.ACTION_VIEW, url)
                    startActivity(intent)
                    true
                } catch (e: Exception) {
                    false
                }
            }

            override fun onReceivedSslError(view: WebView?, handler: SslErrorHandler?, error: SslError?) {
                handler?.proceed()
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                swipeRefresh.isRefreshing = false
                injectStandaloneStyles(view)
                view?.evaluateJavascript("(function() { return sessionStorage.getItem('auth_token') || localStorage.getItem('auth_token') || ''; })();") { token ->
                    val clean = token?.trim('"', ' ', '\'')?.takeIf { it.isNotBlank() && it != "null" }
                    if (clean != null) {
                        val changed = AuthStore.setToken(this@MainActivity, clean)
                        if (changed) {
                            PrintHiveLiveService.triggerImmediatePoll(this@MainActivity)
                        }
                    }
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                if (newProgress > 30) {
                    injectStandaloneStyles(view)
                }
                if (newProgress >= 100) {
                    swipeRefresh.isRefreshing = false
                }
            }

            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                fileUploadCallback?.onReceiveValue(null)
                fileUploadCallback = filePathCallback

                return try {
                    val intent = fileChooserParams?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                        type = "*/*"
                    }
                    filePickerLauncher.launch(intent)
                    true
                } catch (e: Exception) {
                    fileUploadCallback = null
                    false
                }
            }
        }

        webView.setDownloadListener(DownloadListener { url, userAgent, contentDisposition, mimetype, _ ->
            try {
                val request = DownloadManager.Request(Uri.parse(url)).apply {
                    setMimeType(mimetype)
                    addRequestHeader("User-Agent", userAgent)
                    setDescription("Downloading file from PrintHive...")
                    setTitle(url.substringAfterLast("/"))
                    setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, url.substringAfterLast("/"))
                }
                val dm = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                dm.enqueue(request)
                Toast.makeText(this@MainActivity, "Download started...", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                Toast.makeText(this@MainActivity, "Download failed: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        })
    }

    private fun setupBackNavigation() {
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }
}
