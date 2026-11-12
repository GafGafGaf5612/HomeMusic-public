package app.homemusic.pocket

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import java.io.File

/** Runs in its own process (":restart"): waits for the app's process to end, then opens the app again. */
class RestartActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pid = intent.getIntExtra(EXTRA_PID, -1)
        Thread {
            val end = System.currentTimeMillis() + 15_000
            while (pid > 0 && File("/proc/$pid").exists() && System.currentTimeMillis() < end) Thread.sleep(150)
            runOnUiThread {
                startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                finish()
                android.os.Process.killProcess(android.os.Process.myPid())
            }
        }.start()
    }

    companion object {
        const val EXTRA_PID = "pid"
    }
}
