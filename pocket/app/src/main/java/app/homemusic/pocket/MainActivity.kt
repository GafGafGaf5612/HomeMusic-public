package app.homemusic.pocket

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import kotlinx.coroutines.launch

/** The Hub's PWA in a WebView, shown once the service has the servers up; a start screen until then. */
class MainActivity : ComponentActivity() {
    private lateinit var prefs: Prefs
    private lateinit var web: WebView
    private lateinit var status: FrameLayout
    private lateinit var statusText: TextView
    private lateinit var progress: ProgressBar
    private lateinit var retry: Button
    private var loaded = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var waitingForStorage = false

    private val pickFiles = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(r.resultCode, r.data))
        fileCallback = null
    }
    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { start() }

    private val exitReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) = finishAndRemoveTask()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        buildViews()
        ContextCompat.registerReceiver(this, exitReceiver, IntentFilter(PocketService.ACTION_EXIT), ContextCompat.RECEIVER_NOT_EXPORTED)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (loaded && web.canGoBack()) web.goBack() else moveTaskToBack(true)
            }
        })
        PocketBus.mediaCommand = { action, t ->
            val arg = t?.toString() ?: "null"
            web.evaluateJavascript("window.__hmMedia && window.__hmMedia.run('$action', $arg)", null)
        }
        lifecycleScope.launch {
            PocketBus.stateFlow.collect { onState(it) }
        }
        if (prefs.musicRoot == null) chooseMusicRoot() else askNotificationsThenStart()
    }

    override fun onResume() {
        super.onResume()
        if (waitingForStorage) {
            waitingForStorage = false
            val root = if (Environment.isExternalStorageManager()) PocketPaths.sharedMusic() else PocketPaths.privateMusic(this)
            prefs.musicRoot = root.path
            askNotificationsThenStart()
        }
    }

    override fun onDestroy() {
        unregisterReceiver(exitReceiver)
        if (PocketBus.mediaCommand != null) PocketBus.mediaCommand = null
        web.destroy()
        super.onDestroy()
    }

    // ------------------------------------------------------------------------------------------------ first start

    private fun chooseMusicRoot() {
        AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Где хранить музыку?")
            .setMessage(
                "В папке Music/HomeMusic музыка останется, даже если удалить приложение, и её видно по USB. " +
                    "Для этого Android попросит «Доступ ко всем файлам».\n\nВнутри приложения разрешение не нужно, " +
                    "но музыка удалится вместе с приложением."
            )
            .setCancelable(false)
            .setPositiveButton("Music/HomeMusic") { _, _ ->
                if (Environment.isExternalStorageManager()) {
                    prefs.musicRoot = PocketPaths.sharedMusic().path
                    askNotificationsThenStart()
                } else {
                    waitingForStorage = true
                    startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName")))
                }
            }
            .setNegativeButton("Внутри приложения") { _, _ ->
                prefs.musicRoot = PocketPaths.privateMusic(this).path
                askNotificationsThenStart()
            }
            .show()
    }

    private fun askNotificationsThenStart() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        else start()
    }

    private fun start() = PocketService.start(this)

    @SuppressLint("BatteryLife")
    private fun askBatteryOnce() {
        if (prefs.askedBattery) return
        prefs.askedBattery = true
        val pm = getSystemService(PowerManager::class.java)
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
        }
    }

    // ------------------------------------------------------------------------------------------------ state

    private fun onState(s: ServerState) {
        when (s) {
            is ServerState.Starting -> showStatus(s.step, busy = true)
            is ServerState.Failed -> showStatus("Не запустилось:\n${s.message}", busy = false)
            ServerState.Stopped -> if (prefs.musicRoot != null && !loaded) showStatus("Запускаю…", busy = true)
            ServerState.Ready -> {
                web.visibility = View.VISIBLE
                if (!loaded) {
                    showStatus("Открываю…", busy = true)
                    loaded = true
                    web.loadUrl(intent?.data?.takeIf { it.toString().startsWith(Ports.HUB_URL) }?.toString() ?: "${Ports.HUB_URL}/")
                    askBatteryOnce()
                }
            }
        }
    }

    /** The start screen fades out once the page has drawn (no white flash between the two). */
    private fun hideStatus() {
        if (status.visibility != View.VISIBLE || PocketBus.state.value != ServerState.Ready) return
        status.animate().alpha(0f).setDuration(350).withEndAction { status.visibility = View.GONE }.start()
    }

    private fun showStatus(text: String, busy: Boolean) {
        status.animate().cancel()
        status.alpha = 1f
        status.visibility = View.VISIBLE
        statusText.text = text
        progress.visibility = if (busy) View.VISIBLE else View.GONE
        retry.visibility = if (busy) View.GONE else View.VISIBLE
    }

    // ------------------------------------------------------------------------------------------------ views

    @SuppressLint("SetJavaScriptEnabled")
    private fun buildViews() {
        val bg = Color.parseColor("#0E0E10")
        val root = FrameLayout(this).apply { setBackgroundColor(bg) }
        web = WebView(this).apply {
            setBackgroundColor(bg)
            visibility = View.INVISIBLE
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false // the next track starts by itself
            settings.cacheMode = WebSettings.LOAD_DEFAULT
            settings.allowFileAccess = false
            addJavascriptInterface(MediaJs(), MediaJs.NAME)
            if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
                WebViewCompat.addDocumentStartJavaScript(this, MediaJs.POLYFILL, setOf(Ports.HUB_URL))
            }
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                    val url = req.url.toString()
                    if (url.startsWith(Ports.HUB_URL)) return false
                    // links out (a release's page, YouTube): the browser
                    try { startActivity(Intent(Intent.ACTION_VIEW, req.url)) } catch (_: Exception) {}
                    return true
                }

                override fun onPageFinished(view: WebView, url: String) {
                    hideStatus()
                    if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
                        view.evaluateJavascript(MediaJs.POLYFILL, null)
                    }
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                    fileCallback?.onReceiveValue(null)
                    fileCallback = callback
                    val intent = params.createIntent()
                    if (params.mode == FileChooserParams.MODE_OPEN_MULTIPLE) intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                    return try { pickFiles.launch(intent); true } catch (e: Exception) { fileCallback = null; false }
                }
            }
        }
        root.addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        // the start screen: «Богатырь.Музыка» full screen, the server's progress on a dark band at the bottom
        val dp = resources.displayMetrics.density
        status = FrameLayout(this)
        status.addView(ImageView(this).apply {
            setImageResource(R.drawable.splash)
            scaleType = ImageView.ScaleType.CENTER_CROP
        }, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Color.TRANSPARENT, Color.parseColor("#B3000000"), Color.parseColor("#E6000000")))
            setPadding((28 * dp).toInt(), (72 * dp).toInt(), (28 * dp).toInt(), (24 * dp).toInt())
        }
        progress = ProgressBar(this).apply {
            isIndeterminate = true
            indeterminateTintList = android.content.res.ColorStateList.valueOf(Color.WHITE)
        }
        panel.addView(progress, LinearLayout.LayoutParams((36 * dp).toInt(), (36 * dp).toInt()))
        statusText = TextView(this).apply {
            textSize = 16f
            setTextColor(Color.WHITE)
            setShadowLayer(6f, 0f, 1f, Color.BLACK)
            gravity = Gravity.CENTER
        }
        panel.addView(statusText, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = (12 * dp).toInt()
        })
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        retry = Button(this).apply {
            text = "Перезапустить"
            visibility = View.GONE
            setOnClickListener { SettingsActivity.restartApp(this@MainActivity) }
        }
        buttons.addView(retry)
        buttons.addView(Button(this).apply {
            text = "Настройки"
            setOnClickListener { startActivity(Intent(this@MainActivity, SettingsActivity::class.java)) }
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            marginStart = (8 * dp).toInt()
        })
        panel.addView(buttons, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = (12 * dp).toInt()
        })
        status.addView(panel, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        root.addView(status, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        // edge to edge: the picture goes under the system bars; the page and the bottom band keep clear of them
        // (and of the keyboard)
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            web.setPadding(0, 0, 0, 0)
            (web.layoutParams as FrameLayout.LayoutParams).setMargins(bars.left, bars.top, bars.right, bars.bottom)
            web.requestLayout()
            panel.setPadding(panel.paddingLeft, panel.paddingTop, panel.paddingRight, (24 * dp).toInt() + bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        setContentView(root)
    }
}
