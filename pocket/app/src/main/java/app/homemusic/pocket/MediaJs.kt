package app.homemusic.pocket

import android.webkit.JavascriptInterface
import org.json.JSONObject

/**
 * The page's player ↔ Android. The Hub's PWA sets `navigator.mediaSession` (lock screen controls in a browser); a
 * WebView ignores it, so [POLYFILL] replaces it with one that reports here, and runs the page's own action handlers
 * when a media button is pressed (`window.__hmMedia.run`).
 */
class MediaJs {
    @JavascriptInterface
    fun metadata(json: String) {
        val o = if (json.isEmpty()) JSONObject() else JSONObject(json)
        PocketBus.media = PocketBus.media.copy(
            title = o.optString("title"), artist = o.optString("artist"), album = o.optString("album"),
            artwork = o.optString("artwork"),
        )
    }

    @JavascriptInterface
    fun actions(csv: String) {
        PocketBus.media = PocketBus.media.copy(actions = csv.split(',').filter { it.isNotEmpty() }.toSet())
    }

    @JavascriptInterface
    fun state(playing: Boolean, position: Double, duration: Double) {
        val m = PocketBus.media
        if (m.playing == playing && kotlin.math.abs(m.positionS - position) < 1 && m.durationS == duration) return
        PocketBus.media = m.copy(playing = playing, positionS = position, durationS = duration)
    }

    companion object {
        const val NAME = "HMNative"

        val POLYFILL = """
(function () {
  if (window.__hmMedia || !window.$NAME) return;
  var N = window.$NAME, handlers = {}, meta = null, cur = null, last = 0;
  function Meta(init) { init = init || {}; this.title = init.title || ''; this.artist = init.artist || '';
    this.album = init.album || ''; this.artwork = init.artwork || []; }
  window.MediaMetadata = Meta;
  var ms = {
    get metadata() { return meta; },
    set metadata(m) {
      meta = m;
      try { N.metadata(m ? JSON.stringify({ title: m.title || '', artist: m.artist || '', album: m.album || '',
        artwork: (m.artwork && m.artwork[0] && m.artwork[0].src) || '' }) : ''); } catch (e) {}
    },
    playbackState: 'none',
    setActionHandler: function (a, h) {
      handlers[a] = h || null;
      try { N.actions(Object.keys(handlers).filter(function (k) { return handlers[k]; }).join(',')); } catch (e) {}
    },
    setPositionState: function () {},
    setMicrophoneActive: function () {}, setCameraActive: function () {}
  };
  try { Object.defineProperty(navigator, 'mediaSession', { value: ms, configurable: true }); } catch (e) {}
  function report(el) {
    cur = el;
    try { N.state(!el.paused && !el.ended, el.currentTime || 0, isFinite(el.duration) ? el.duration : 0); } catch (e) {}
  }
  function hook(el) {
    if (el.__hm) return; el.__hm = 1;
    ['play', 'playing', 'pause', 'ended', 'seeked', 'durationchange', 'emptied'].forEach(function (ev) {
      el.addEventListener(ev, function () { report(el); });
    });
    el.addEventListener('timeupdate', function () { var n = Date.now(); if (n - last > 5000) { last = n; report(el); } });
  }
  var play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () { hook(this); return play.apply(this, arguments); };
  document.addEventListener('play', function (e) { if (e.target instanceof HTMLMediaElement) hook(e.target); }, true);
  window.__hmMedia = {
    run: function (a, t) {
      var h = handlers[a];
      if (h) { h(a === 'seekto' ? { action: a, seekTime: t } : { action: a }); return; }
      if (!cur) return;
      if (a === 'play') cur.play(); else if (a === 'pause') cur.pause();
      else if (a === 'seekto' && t != null) cur.currentTime = t;
    }
  };
})();
""".trimIndent()
    }
}
