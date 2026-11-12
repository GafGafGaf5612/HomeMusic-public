package app.homemusic.pocket

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.support.v4.media.MediaMetadataCompat
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import android.util.Log
import android.webkit.CookieManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.media.app.NotificationCompat.MediaStyle
import app.homemusic.pocket.torrent.QbitServer
import app.homemusic.pocket.torrent.TorrentEngine
import java.net.HttpURLConnection
import java.net.URL

/**
 * Keeps HomeMusic's servers running on the phone — Navidrome (child process), the Hub (Python, in this process), the
 * torrent client — and owns the notification: the server's status, or the page's player with its buttons, which also
 * feeds the media session (lock screen, headset buttons, Bluetooth).
 */
class PocketService : Service() {
    private val tag = "PocketService"
    private val main = Handler(Looper.getMainLooper())
    private lateinit var prefs: Prefs
    private var navidrome: ChildProcess? = null
    private var engine: TorrentEngine? = null
    private var qbit: QbitServer? = null
    private var session: MediaSessionCompat? = null
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var started = false
    @Volatile private var stopping = false
    private var artUrl = ""
    private var art: Bitmap? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        PocketLog.init(filesDir)
        channel()
        session = MediaSessionCompat(this, "HomeMusic").apply {
            setCallback(object : MediaSessionCompat.Callback() {
                override fun onPlay() = command("play")
                override fun onPause() = command("pause")
                override fun onSkipToNext() = command("nexttrack")
                override fun onSkipToPrevious() = command("previoustrack")
                override fun onSeekTo(pos: Long) = command("seekto", pos / 1000.0)
                override fun onStop() = command("pause")
            })
        }
        PocketBus.mediaListener = { info -> main.post { onMedia(info) } }
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "HomeMusic:servers").apply { setReferenceCounted(false) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_EXIT -> { exit(); return START_NOT_STICKY }
            ACTION_PLAY_PAUSE -> command(if (PocketBus.media.playing) "pause" else "play")
            ACTION_NEXT -> command("nexttrack")
            ACTION_PREV -> command("previoustrack")
        }
        foreground()
        if (!started) {
            started = true
            Thread({ startServers() }, "pocket-start").start()
        }
        return START_STICKY
    }

    private fun foreground() {
        val n = notification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else startForeground(NOTIFICATION_ID, n)
    }

    // ------------------------------------------------------------------------------------------------ servers

    private fun startServers() {
        val root = prefs.musicRoot ?: return fail("Не выбрана папка для музыки")
        val paths = PocketPaths(this, root)
        try {
            step("Готовлю файлы")
            getExternalFilesDir(null) // Android makes Android/data/<package> only through this call
            listOf(paths.library, paths.torrents, paths.hubData, paths.torrentState).forEach { it.mkdirs() }
            if (!paths.library.isDirectory) return fail("Нет доступа к папке музыки: ${paths.music}")
            Servers.unpackHub(this, paths, prefs)
            Servers.linkBinaries(paths)

            step("Запускаю торрент-клиент")
            engine = TorrentEngine(paths.torrentState).also { it.start() }
            qbit = QbitServer(engine!!, Ports.QBIT, prefs.secret("qbit_password")).also { it.start(5000, true) }

            step("Запускаю Navidrome")
            navidrome = Servers.navidrome(paths, prefs.secret("navidrome_password")).also { it.start() }
            if (!Servers.waitFor("http://127.0.0.1:${Ports.NAVIDROME}/ping", 90) { navidrome?.alive == true }) {
                return fail("Navidrome не запустился:\n${navidrome?.lastLines()}")
            }

            step("Запускаю Хаб (первый раз — до минуты)")
            Servers.startPython(this)
            Servers.startHub(paths, Servers.hubEnv(paths, prefs)) { e ->
                if (!stopping) fail("Хаб остановился${e?.let { ": ${it.message}" } ?: ""}")
            }
            if (!Servers.waitFor("${Ports.HUB_URL}/api/auth/options", 240)) return fail("Хаб не ответил за 4 минуты")

            step("Вхожу в Хаб")
            val token = Servers.hubLogin(prefs) ?: return fail("Хаб не принял пароль Navidrome")
            main.post {
                val cm = CookieManager.getInstance()
                cm.setCookie(Ports.HUB_URL, "hm_session=$token; Path=/; Max-Age=5184000; HttpOnly")
                cm.flush()
                PocketBus.state.value = ServerState.Ready
                updateNotification()
            }
            watch()
        } catch (e: Throwable) {
            PocketLog.e(tag, "start", e)
            fail(e.message ?: e.toString())
        }
    }

    /** Keeps an eye on the servers: resume data of torrents, a dead Navidrome, the CPU while something downloads. */
    private fun watch() {
        var ticks = 0
        while (!stopping) {
            Thread.sleep(5000)
            ticks++
            try {
                engine?.saveChanged()
                if (navidrome?.alive == false && !stopping) {
                    PocketLog.w(tag, "navidrome died, restarting: ${navidrome?.lastLines()}")
                    navidrome?.start()
                }
                // torrents and the Hub's downloads go on with the screen off: hold the CPU while torrents run
                val busy = (engine?.activeCount() ?: 0) > 0
                if (busy) wakeLock?.acquire(10 * 60 * 1000L) else if (wakeLock?.isHeld == true) wakeLock?.release()
                if (ticks % 6 == 0) main.post { updateNotification() }
            } catch (e: Throwable) {
                PocketLog.w(tag, "watch", e)
            }
        }
    }

    private fun step(text: String) {
        PocketLog.i(tag, text)
        PocketBus.state.value = ServerState.Starting(text)
        main.post { updateNotification() }
    }

    private fun fail(message: String) {
        PocketLog.e(tag, message)
        PocketBus.state.value = ServerState.Failed(message)
        main.post { updateNotification() }
    }

    private fun exit() {
        stopping = true
        PocketBus.state.value = ServerState.Stopped
        Thread({
            Servers.stopHub()
            navidrome?.stop()
            qbit?.stop()
            engine?.stop()
            main.post {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                // the Python interpreter can't be started twice in one process: the next launch gets a new one
                main.postDelayed({ android.os.Process.killProcess(android.os.Process.myPid()) }, 300)
            }
        }, "pocket-stop").start()
        sendBroadcast(Intent(ACTION_EXIT).setPackage(packageName))
    }

    override fun onDestroy() {
        if (!stopping) {
            stopping = true
            Servers.stopHub()
            navidrome?.stop()
            qbit?.stop()
            engine?.stop()
        }
        unregisterNoisy()
        session?.release()
        if (wakeLock?.isHeld == true) wakeLock?.release()
        PocketBus.mediaListener = null
        super.onDestroy()
    }

    // ------------------------------------------------------------------------------------------------ player

    private fun command(action: String, seekTime: Double? = null) {
        main.post { PocketBus.mediaCommand?.invoke(action, seekTime) }
    }

    private fun onMedia(info: MediaInfo) {
        val s = session ?: return
        if (info.hasTrack) {
            if (info.artwork != artUrl) {
                artUrl = info.artwork
                art = null
                if (artUrl.isNotEmpty()) loadArt(artUrl)
            }
            s.setMetadata(
                MediaMetadataCompat.Builder()
                    .putString(MediaMetadataCompat.METADATA_KEY_TITLE, info.title)
                    .putString(MediaMetadataCompat.METADATA_KEY_ARTIST, info.artist)
                    .putString(MediaMetadataCompat.METADATA_KEY_ALBUM, info.album)
                    .putLong(MediaMetadataCompat.METADATA_KEY_DURATION, (info.durationS * 1000).toLong())
                    .also { b -> art?.let { b.putBitmap(MediaMetadataCompat.METADATA_KEY_ALBUM_ART, it) } }
                    .build()
            )
            var actions = PlaybackStateCompat.ACTION_PLAY or PlaybackStateCompat.ACTION_PAUSE or
                PlaybackStateCompat.ACTION_PLAY_PAUSE or PlaybackStateCompat.ACTION_SEEK_TO or PlaybackStateCompat.ACTION_STOP
            if ("nexttrack" in info.actions) actions = actions or PlaybackStateCompat.ACTION_SKIP_TO_NEXT
            if ("previoustrack" in info.actions) actions = actions or PlaybackStateCompat.ACTION_SKIP_TO_PREVIOUS
            s.setPlaybackState(
                PlaybackStateCompat.Builder()
                    .setActions(actions)
                    .setState(
                        if (info.playing) PlaybackStateCompat.STATE_PLAYING else PlaybackStateCompat.STATE_PAUSED,
                        (info.positionS * 1000).toLong(), if (info.playing) 1f else 0f,
                    )
                    .build()
            )
            s.isActive = true
            if (info.playing) registerNoisy() else unregisterNoisy()
        } else {
            s.isActive = false
            unregisterNoisy()
        }
        updateNotification()
    }

    /** The cover for the lock screen: the Hub's URL, fetched with the WebView's login cookie. */
    private fun loadArt(url: String) {
        Thread({
            try {
                val abs = if (url.startsWith("http")) url else Ports.HUB_URL + url
                if (!abs.startsWith(Ports.HUB_URL)) return@Thread // only our own server's covers
                val conn = URL(abs).openConnection() as HttpURLConnection
                CookieManager.getInstance().getCookie(Ports.HUB_URL)?.let { conn.setRequestProperty("Cookie", it) }
                conn.connectTimeout = 3000
                conn.readTimeout = 10000
                val bmp = conn.inputStream.use { BitmapFactory.decodeStream(it) }
                conn.disconnect()
                main.post { if (artUrl == url) { art = bmp; onMedia(PocketBus.media) } }
            } catch (e: Exception) {
                Log.w(tag, "cover $url: $e")
            }
        }, "cover").start()
    }

    private var noisy: BroadcastReceiver? = null

    /** Headphones unplugged / Bluetooth gone while playing: pause, like any player. */
    private fun registerNoisy() {
        if (noisy != null) return
        noisy = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, i: Intent?) = command("pause")
        }
        ContextCompat.registerReceiver(this, noisy, IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY), ContextCompat.RECEIVER_NOT_EXPORTED)
    }

    private fun unregisterNoisy() {
        noisy?.let { try { unregisterReceiver(it) } catch (_: Exception) {} }
        noisy = null
    }

    // ------------------------------------------------------------------------------------------------ notification

    private fun channel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "HomeMusic", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Сервер музыки на телефоне и плеер"
                setShowBadge(false)
            }
        )
    }

    private fun updateNotification() {
        if (stopping) return
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification())
    }

    private fun pending(action: String, code: Int): PendingIntent =
        PendingIntent.getService(this, code, Intent(this, PocketService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE)

    private fun notification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE,
        )
        val b = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_note)
            .setContentIntent(open)
            .setOngoing(true)
            .setShowWhen(false)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        val m = PocketBus.media
        if (m.hasTrack && PocketBus.state.value == ServerState.Ready) {
            b.setContentTitle(m.title).setContentText(m.artist).setLargeIcon(art)
                .addAction(R.drawable.ic_prev, "Назад", pending(ACTION_PREV, 1))
                .addAction(if (m.playing) R.drawable.ic_pause else R.drawable.ic_play, if (m.playing) "Пауза" else "Играть", pending(ACTION_PLAY_PAUSE, 2))
                .addAction(R.drawable.ic_next, "Дальше", pending(ACTION_NEXT, 3))
                .setStyle(MediaStyle().setMediaSession(session?.sessionToken).setShowActionsInCompactView(0, 1, 2))
        } else {
            val text = when (val s = PocketBus.state.value) {
                is ServerState.Starting -> s.step
                is ServerState.Failed -> "Ошибка: ${s.message.lineSequence().first()}"
                ServerState.Ready -> engine?.activeCount()?.takeIf { it > 0 }?.let { "Качаются раздачи: $it" } ?: "Navidrome и Хаб работают"
                ServerState.Stopped -> "Остановлен"
            }
            b.setContentTitle("HomeMusic").setContentText(text)
        }
        b.addAction(R.drawable.ic_close, "Выключить", pending(ACTION_EXIT, 4))
        return b.build()
    }

    companion object {
        const val CHANNEL = "pocket"
        const val NOTIFICATION_ID = 1
        const val ACTION_EXIT = "app.homemusic.pocket.EXIT"
        const val ACTION_PLAY_PAUSE = "app.homemusic.pocket.PLAY_PAUSE"
        const val ACTION_NEXT = "app.homemusic.pocket.NEXT"
        const val ACTION_PREV = "app.homemusic.pocket.PREV"

        fun start(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, PocketService::class.java))
        }
    }
}
