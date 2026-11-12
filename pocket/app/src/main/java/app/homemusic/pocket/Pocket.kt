package app.homemusic.pocket

import android.content.Context
import android.content.SharedPreferences
import android.os.Environment
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.io.File
import java.security.SecureRandom

/** Loopback ports of the three servers the app runs (the same as in docker-compose.yml). */
object Ports {
    const val HUB = 8080
    const val NAVIDROME = 4533
    const val QBIT = 8081
    const val HUB_URL = "http://127.0.0.1:$HUB"
}

/** Settings and generated secrets (never leave the phone). */
class Prefs(ctx: Context) {
    private val p: SharedPreferences = ctx.getSharedPreferences("pocket", Context.MODE_PRIVATE)

    /** A random secret made on the first use and kept: Navidrome's admin password, the Hub's key, the torrent API's. */
    fun secret(key: String): String = p.getString(key, null) ?: randomToken().also { p.edit().putString(key, it).apply() }

    /** The Hub's HUB_SECRET_KEY: a Fernet key (32 random bytes, URL-safe base64), made once. */
    fun fernetKey(key: String): String = p.getString(key, null) ?: run {
        val b = ByteArray(32)
        SecureRandom().nextBytes(b)
        android.util.Base64.encodeToString(b, android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP)
            .also { p.edit().putString(key, it).apply() }
    }

    var musicRoot: String?
        get() = p.getString("music_root", null)
        set(v) = p.edit().putString("music_root", v).apply()
    var llmApiKey: String
        get() = p.getString("llm_api_key", "") ?: ""
        set(v) = p.edit().putString("llm_api_key", v.trim()).apply()
    var lastfmApiKey: String
        get() = p.getString("lastfm_api_key", "") ?: ""
        set(v) = p.edit().putString("lastfm_api_key", v.trim()).apply()
    var askedBattery: Boolean
        get() = p.getBoolean("asked_battery", false)
        set(v) = p.edit().putBoolean("asked_battery", v).apply()
    var hubStamp: Long
        get() = p.getLong("hub_stamp", 0)
        set(v) = p.edit().putLong("hub_stamp", v).apply()

    private fun randomToken(): String {
        val b = ByteArray(24)
        SecureRandom().nextBytes(b)
        return b.joinToString("") { "%02x".format(it) }
    }
}

/** Where everything lives on the phone. */
class PocketPaths(ctx: Context, musicRoot: String) {
    val files: File = ctx.filesDir
    val cache: File = ctx.cacheDir
    val nativeLibs = File(ctx.applicationInfo.nativeLibraryDir)
    val bin = File(files, "bin") // ffmpeg, ffprobe, qjs: symlinks into nativeLibs, on the Hub's PATH
    val hub = File(files, "hub") // the Hub's code (assets/hub.zip)
    val hubData = File(files, "hub-data") // its SQLite database and config.yaml
    val navidromeData = File(files, "navidrome")
    val torrentState = File(files, "torrents")
    val music = File(musicRoot) // MUSIC_ROOT: library/, staging/ (torrents in staging/torrents), .trash/, .backups/
    val library = File(music, "library")
    val torrents = File(music, "staging/torrents")

    companion object {
        /** Music/HomeMusic in shared storage (kept across reinstalls, visible over USB) — needs "All files access". */
        fun sharedMusic(): File = File(Environment.getExternalStorageDirectory(), "Music/HomeMusic")

        /** Inside the app's own storage: no permission, but removed with the app. */
        fun privateMusic(ctx: Context): File = File(ctx.getExternalFilesDir(null) ?: ctx.filesDir, "Music")
    }
}

/** What the service is doing, for the start screen and the notification. */
sealed class ServerState {
    data class Starting(val step: String) : ServerState()
    data object Ready : ServerState()
    data class Failed(val message: String) : ServerState()
    data object Stopped : ServerState()
}

/** In-process links between the service, the player bridge and the activity. */
object PocketBus {
    val state = MutableStateFlow<ServerState>(ServerState.Stopped)
    val stateFlow: StateFlow<ServerState> get() = state

    /** A media button or the notification asks the page's player to do something ("play", "pause", "nexttrack"…). */
    @Volatile var mediaCommand: ((action: String, seekTime: Double?) -> Unit)? = null

    /** The page's player state, reported by [MediaJs]; the service shows it in the media notification. */
    @Volatile var mediaListener: ((MediaInfo) -> Unit)? = null
    @Volatile var media = MediaInfo()
        set(v) {
            field = v
            mediaListener?.invoke(v)
        }
}

data class MediaInfo(
    val title: String = "",
    val artist: String = "",
    val album: String = "",
    val artwork: String = "",
    val playing: Boolean = false,
    val positionS: Double = 0.0,
    val durationS: Double = 0.0,
    val actions: Set<String> = emptySet(),
) {
    val hasTrack get() = title.isNotEmpty()
}
