package app.homemusic.pocket

import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** The app's log in logcat and in files/logs/ (adb shell run-as app.homemusic.pocket cat files/logs/pocket.log). */
object PocketLog {
    @Volatile private var dir: File? = null
    private val time = SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US)

    fun init(filesDir: File) {
        dir = File(filesDir, "logs").apply { mkdirs() }
    }

    fun i(tag: String, msg: String) = write("pocket.log", tag, "I", msg).also { Log.i(tag, msg) }
    fun w(tag: String, msg: String, e: Throwable? = null) =
        write("pocket.log", tag, "W", msg + (e?.let { "\n" + Log.getStackTraceString(it) } ?: "")).also { Log.w(tag, msg, e) }
    fun e(tag: String, msg: String, e: Throwable? = null) =
        write("pocket.log", tag, "E", msg + (e?.let { "\n" + Log.getStackTraceString(it) } ?: "")).also { Log.e(tag, msg, e) }

    /** A child process's output: a file of its own. */
    fun line(file: String, line: String) = write(file, "", "", line)

    @Synchronized
    private fun write(name: String, tag: String, level: String, msg: String) {
        val d = dir ?: return
        try {
            val f = File(d, name)
            if (f.length() > 4_000_000) f.renameTo(File(d, "$name.1"))
            f.appendText(if (tag.isEmpty()) "$msg\n" else "${time.format(Date())} $level/$tag: $msg\n")
        } catch (_: Exception) {
        }
    }
}
