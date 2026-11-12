package app.homemusic.pocket

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat

/**
 * What the Hub reads from .env on a server and a phone user may want to set: the assistant's OpenRouter key and the
 * Last.fm key for mixes. Saving restarts the app (the Hub reads its settings once).
 */
class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val dp = resources.displayMetrics.density
        val pad = (20 * dp).toInt()
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }

        fun label(text: String, size: Float = 14f, color: String = "#B8B8C0") = TextView(this).apply {
            this.text = text; textSize = size; setTextColor(Color.parseColor(color)); setPadding(0, (14 * dp).toInt(), 0, (4 * dp).toInt())
        }

        fun field(value: String, hint: String) = EditText(this).apply {
            setText(value); this.hint = hint; setSingleLine()
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            setTextColor(Color.WHITE); setHintTextColor(Color.parseColor("#6A6A72"))
        }

        col.addView(label("Настройки HomeMusic", 22f, "#FFFFFF"))
        col.addView(label("Музыка хранится в: ${prefs.musicRoot ?: "ещё не выбрано"}"))
        col.addView(label("Ключ OpenRouter для ИИ-ассистента (LLM_API_KEY)"))
        val llm = field(prefs.llmApiKey, "sk-or-…")
        col.addView(llm)
        col.addView(label("Ключ Last.fm для «Микса дня» (LASTFM_API_KEY)"))
        val lastfm = field(prefs.lastfmApiKey, "необязательно")
        col.addView(lastfm)
        col.addView(label(
            "Navidrome работает на http://127.0.0.1:${Ports.NAVIDROME}, логин admin. Пароль для Tempus или другого " +
                "Subsonic-плеера на этом телефоне — кнопка ниже копирует его."
        ))
        col.addView(Button(this).apply {
            text = "Скопировать пароль Navidrome"
            setOnClickListener {
                val cm = getSystemService(android.content.ClipboardManager::class.java)
                cm.setPrimaryClip(android.content.ClipData.newPlainText("Navidrome", prefs.secret("navidrome_password")))
                text = "Скопировано"
            }
        })
        col.addView(Button(this).apply {
            text = "Сохранить и перезапустить"
            setOnClickListener {
                prefs.llmApiKey = llm.text.toString()
                prefs.lastfmApiKey = lastfm.text.toString()
                restartApp(this@SettingsActivity)
            }
        })
        col.addView(Button(this).apply {
            text = "Выключить HomeMusic"
            setOnClickListener {
                startService(Intent(this@SettingsActivity, PocketService::class.java).setAction(PocketService.ACTION_EXIT))
                finishAffinity()
            }
        })

        val scroll = ScrollView(this).apply { setBackgroundColor(Color.parseColor("#0E0E10")); addView(col) }
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        setContentView(scroll, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
    }

    companion object {
        /** Stops the servers and starts the app again in a new process (Python can only start once per process):
         *  [RestartActivity], in a process of its own, waits for this one to end. */
        fun restartApp(ctx: Context) {
            ctx.startService(Intent(ctx, PocketService::class.java).setAction(PocketService.ACTION_EXIT))
            ctx.startActivity(
                Intent(ctx, RestartActivity::class.java)
                    .putExtra(RestartActivity.EXTRA_PID, android.os.Process.myPid())
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }
}
