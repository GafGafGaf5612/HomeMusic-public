package app.homemusic.pocket

import android.content.Context
import android.os.Build
import android.system.Os
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.TimeZone
import java.util.zip.ZipInputStream

private const val TAG = "Pocket"

/** A server the app runs as a child process (Navidrome): its output goes to logcat, the tail is kept for errors. */
class ChildProcess(private val name: String, private val cmd: List<String>, private val env: Map<String, String>, private val dir: File) {
    private var proc: Process? = null
    private val tail = ArrayDeque<String>()

    fun start() {
        killStale(File(cmd[0]).name)
        val pb = ProcessBuilder(cmd).directory(dir).redirectErrorStream(true)
        pb.environment().putAll(env)
        val p = pb.start()
        proc = p
        Thread({
            p.inputStream.bufferedReader().forEachLine { line ->
                Log.i("Pocket-$name", line)
                PocketLog.line("$name.log", line)
                synchronized(tail) { tail.addLast(line); if (tail.size > 30) tail.removeFirst() }
            }
            val code = p.waitFor()
            PocketLog.w(TAG, "$name exited with code $code")
        }, "$name-log").start()
    }

    val alive get() = proc?.isAlive == true
    fun lastLines(): String {
        val p = proc
        val exit = if (p != null && !p.isAlive) "код выхода ${p.exitValue()}" + (if (p.exitValue() == 159) " (SIGSYS: seccomp)" else "") + "\n" else ""
        return exit + synchronized(tail) { tail.takeLast(6).joinToString("\n") }
    }

    fun stop() {
        val p = proc ?: return
        p.destroy()
        if (!p.waitFor(3, java.util.concurrent.TimeUnit.SECONDS)) p.destroyForcibly()
        proc = null
    }

    /** A copy left running by an app process that died (it would hold the port). Same uid, so we may kill it. */
    private fun killStale(exe: String) {
        val me = android.os.Process.myPid()
        File("/proc").listFiles { f -> f.name.all(Char::isDigit) }?.forEach { d ->
            val pid = d.name.toInt()
            if (pid == me) return@forEach
            val cmdline = try { File(d, "cmdline").readText() } catch (e: Exception) { return@forEach }
            if (cmdline.substringBefore('\u0000').endsWith(exe)) {
                PocketLog.w(TAG, "killing a stale $exe (pid $pid)")
                android.os.Process.killProcess(pid)
            }
        }
    }
}

object Servers {
    /** Unpacks the Hub's code (assets/hub.zip) after an install or an update of the app. */
    fun unpackHub(ctx: Context, paths: PocketPaths, prefs: Prefs) {
        val stamp = ctx.packageManager.getPackageInfo(ctx.packageName, 0).lastUpdateTime
        if (prefs.hubStamp == stamp && File(paths.hub, "app/main.py").isFile) return
        paths.hub.deleteRecursively()
        paths.hub.mkdirs()
        ZipInputStream(ctx.assets.open("hub.zip").buffered()).use { zip ->
            while (true) {
                val e = zip.nextEntry ?: break
                val out = File(paths.hub, e.name).canonicalFile
                require(out.path.startsWith(paths.hub.canonicalPath)) { "bad path in hub.zip: ${e.name}" }
                if (e.isDirectory) out.mkdirs() else { out.parentFile?.mkdirs(); out.outputStream().use { zip.copyTo(it) } }
            }
        }
        prefs.hubStamp = stamp
    }

    /** `ffmpeg`, `ffprobe`, `qjs` on the Hub's PATH: links to the executables Android unpacked from the APK. */
    fun linkBinaries(paths: PocketPaths) {
        paths.bin.mkdirs()
        for ((name, lib) in listOf("ffmpeg" to "libffmpeg.so", "ffprobe" to "libffprobe.so", "qjs" to "libqjs.so")) {
            val link = File(paths.bin, name)
            link.delete()
            Os.symlink(File(paths.nativeLibs, lib).path, link.path)
        }
    }

    fun navidrome(paths: PocketPaths, password: String): ChildProcess {
        paths.navidromeData.mkdirs()
        paths.library.mkdirs()
        val env = mapOf(
            "ND_MUSICFOLDER" to paths.library.path,
            "ND_DATAFOLDER" to paths.navidromeData.path,
            "ND_CACHEFOLDER" to File(paths.cache, "navidrome").path,
            "ND_ADDRESS" to "127.0.0.1",
            "ND_PORT" to Ports.NAVIDROME.toString(),
            "ND_DEVAUTOCREATEADMINPASSWORD" to password, // the admin "admin" is made on the first start
            "ND_FFMPEGPATH" to File(paths.bin, "ffmpeg").path,
            "ND_ENABLEINSIGHTSCOLLECTOR" to "false",
            "ND_ENABLEEXTERNALSERVICES" to "false", // no Linux DNS here: Last.fm/Spotify lookups would only fail
            "ND_SCANNER_SCHEDULE" to "@every 6h",
            "ND_LOGLEVEL" to "info",
            "HOME" to paths.files.path,
            "TMPDIR" to paths.cache.path,
        )
        return ChildProcess("navidrome", listOf(File(paths.nativeLibs, "libnavidrome.so").path), env, paths.navidromeData)
    }

    fun hubEnv(paths: PocketPaths, prefs: Prefs): Map<String, String> {
        paths.hubData.mkdirs()
        writeHubConfig(paths)
        val path = listOfNotNull(paths.bin.path, System.getenv("PATH")).joinToString(":")
        return mapOf(
            "MUSIC_ROOT" to paths.music.path,
            "DATA_DIR" to paths.hubData.path,
            "CONFIG_FILE" to File(paths.hubData, "config.yaml").path,
            "STATIC_DIR" to File(paths.hub, "app/static").path,
            "NAVIDROME_URL" to "http://127.0.0.1:${Ports.NAVIDROME}",
            "NAVIDROME_ADMIN_USER" to "admin",
            "NAVIDROME_ADMIN_PASSWORD" to prefs.secret("navidrome_password"),
            "HUB_SECRET_KEY" to prefs.fernetKey("hub_fernet_key"),
            "EMBEDDER_URL" to "", // the audio vector model needs a GPU: mixes are built without the audio term
            "TORRENT_SEARCH" to "public",
            "QBIT_URL" to "http://127.0.0.1:${Ports.QBIT}",
            "QBIT_USERNAME" to "admin",
            "QBIT_PASSWORD" to prefs.secret("qbit_password"),
            "LLM_API_KEY" to prefs.llmApiKey,
            "LASTFM_API_KEY" to prefs.lastfmApiKey,
            "FFMPEG_BIN" to File(paths.bin, "ffmpeg").path,
            "FFPROBE_BIN" to File(paths.bin, "ffprobe").path,
            "TIMEZONE" to TimeZone.getDefault().id,
            "TZ" to TimeZone.getDefault().id,
            "PATH" to path,
            "HOME" to paths.files.path,
            "TMPDIR" to paths.cache.path,
            "LOG_LEVEL" to "INFO",
            "HUB_PORT" to Ports.HUB.toString(),
            "POCKET_LOG_DIR" to File(paths.files, "logs").path,
            "HOMEMUSIC_POCKET" to Build.MODEL,
        )
    }

    /** config.yaml with what differs on a phone; written once, so a hand edit (adb) stays. */
    private fun writeHubConfig(paths: PocketPaths) {
        val f = File(paths.hubData, "config.yaml")
        if (f.isFile) return
        f.writeText(
            """
            |# HomeMusic on the phone (pocket/): what differs from config.example.yaml
            |jobs:
            |  workers: 2
            |torrents:
            |  save_path: "${paths.torrents.path}"   # the built-in torrent client writes where the Hub reads
            |  keep_seeding: false                  # a phone doesn't seed: the files move into the library
            |  admin_only: false
            |""".trimMargin()
        )
    }

    fun startPython(ctx: Context) {
        if (!Python.isStarted()) Python.start(AndroidPlatform(ctx))
    }

    /** Runs the Hub (uvicorn) on its own thread; returns at once. */
    fun startHub(paths: PocketPaths, env: Map<String, String>, onExit: (Throwable?) -> Unit) {
        Thread({
            try {
                Python.getInstance().getModule("pocket_main").callAttr("run", paths.hub.path, JSONObject(env).toString())
                onExit(null)
            } catch (e: Throwable) {
                PocketLog.e(TAG, "hub crashed", e)
                onExit(e)
            }
        }, "hub").start()
    }

    fun stopHub() {
        if (Python.isStarted()) try { Python.getInstance().getModule("pocket_main").callAttr("stop") } catch (_: Throwable) {}
    }

    /** HTTP status of a GET on loopback, or -1 when nothing answers. */
    fun status(url: String, timeoutMs: Int = 2000): Int = try {
        (URL(url).openConnection() as HttpURLConnection).run {
            connectTimeout = timeoutMs
            readTimeout = timeoutMs
            val code = responseCode
            disconnect()
            code
        }
    } catch (e: Exception) {
        -1
    }

    fun waitFor(url: String, timeoutS: Int, alive: () -> Boolean = { true }): Boolean {
        val end = System.currentTimeMillis() + timeoutS * 1000L
        while (System.currentTimeMillis() < end) {
            if (status(url) in 200..499) return true
            if (!alive()) return false
            Thread.sleep(400)
        }
        return false
    }

    /** Logs the Hub in as Navidrome's admin; returns the session cookie value for the WebView. */
    fun hubLogin(prefs: Prefs): String? = try {
        (URL("${Ports.HUB_URL}/api/auth/login").openConnection() as HttpURLConnection).run {
            requestMethod = "POST"
            doOutput = true
            connectTimeout = 5000
            readTimeout = 30000
            setRequestProperty("Content-Type", "application/json")
            outputStream.use {
                it.write(JSONObject().put("username", "admin").put("password", prefs.secret("navidrome_password")).toString().toByteArray())
            }
            val cookie = if (responseCode == 200) headerFields["Set-Cookie"]?.firstOrNull { it.startsWith("hm_session=") } else null
            if (cookie == null) PocketLog.w(TAG, "hub login: $responseCode ${errorStream?.bufferedReader()?.readText()}")
            disconnect()
            cookie?.substringBefore(';')?.substringAfter('=')
        }
    } catch (e: Exception) {
        PocketLog.w(TAG, "hub login", e)
        null
    }
}
