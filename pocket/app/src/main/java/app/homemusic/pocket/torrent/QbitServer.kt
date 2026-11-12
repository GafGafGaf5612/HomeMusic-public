package app.homemusic.pocket.torrent

import android.util.Log
import fi.iki.elonen.NanoHTTPD
import org.json.JSONArray
import java.io.ByteArrayInputStream
import java.io.File
import java.security.MessageDigest
import java.util.UUID

/**
 * qBittorrent's Web API v2 — the part the Hub's client uses (hub/app/torrents/qbit.py) — over [TorrentEngine], on
 * 127.0.0.1 only. Other apps on the phone can reach loopback too, so it asks for a login (the Hub has the password).
 */
class QbitServer(
    private val engine: TorrentEngine,
    port: Int,
    private val password: String,
) : NanoHTTPD("127.0.0.1", port) {
    private val tag = "PocketQbit"
    private val sid = UUID.randomUUID().toString().replace("-", "")

    override fun serve(session: IHTTPSession): Response = try {
        val files = HashMap<String, String>()
        if (session.method == Method.POST) session.parseBody(files)
        val path = session.uri.removePrefix("/api/v2/")
        val p = session.parameters.mapValues { it.value.firstOrNull() ?: "" }
        if (path == "auth/login") login(p)
        else if (!authorized(session)) text(Response.Status.FORBIDDEN, "Forbidden")
        else route(path, p, files)
    } catch (e: Exception) {
        app.homemusic.pocket.PocketLog.w(tag, "${session.method} ${session.uri}", e)
        text(Response.Status.INTERNAL_ERROR, e.message ?: e.toString())
    }

    private fun login(p: Map<String, String>): Response {
        if (!MessageDigest.isEqual(p["password"].orEmpty().toByteArray(), password.toByteArray())) return text(Response.Status.OK, "Fails.")
        return text(Response.Status.OK, "Ok.").also { it.addHeader("Set-Cookie", "SID=$sid; HttpOnly; path=/") }
    }

    private fun authorized(s: IHTTPSession): Boolean = s.cookies.read("SID") == sid

    private fun route(path: String, p: Map<String, String>, files: Map<String, String>): Response {
        val hash = p["hash"].orEmpty().lowercase()
        val hashes = p["hashes"].orEmpty().lowercase().split('|').filter { it.isNotEmpty() }
        return when (path) {
            "app/version" -> text(Response.Status.OK, "v5.0.0")
            "app/webapiVersion" -> text(Response.Status.OK, "2.11.0")
            "transfer/info" -> json(engine.transferInfo().toString())
            "torrents/add" -> add(p, files)
            "torrents/info" -> json(engine.info(hashes.toSet().ifEmpty { null }.takeIf { p.containsKey("hashes") }, p["category"]).toString())
            "torrents/files" -> engine.files(hash)?.let { json(it.toString()) } ?: notFound()
            "torrents/properties" -> engine.properties(hash)?.let { json(it.toString()) } ?: notFound()
            "torrents/export" -> engine.export(hash)?.let {
                newFixedLengthResponse(Response.Status.OK, "application/x-bittorrent", ByteArrayInputStream(it), it.size.toLong())
            } ?: notFound()
            "torrents/pieceStates" -> engine.pieceStates(hash)?.let { json(JSONArray(it.toList()).toString()) } ?: notFound()
            "torrents/filePrio" -> {
                if (!engine.exists(hash)) return notFound()
                engine.setFilePriority(hash, p["id"].orEmpty().split('|').mapNotNull { it.toIntOrNull() }, p["priority"]?.toIntOrNull() ?: 1)
                ok()
            }
            "torrents/start", "torrents/resume" -> each(hashes) { engine.start(it) }
            "torrents/stop", "torrents/pause" -> each(hashes) { engine.stopTorrent(it) }
            "torrents/toggleSequentialDownload" -> each(hashes) { engine.toggleSequential(it) }
            "torrents/toggleFirstLastPiecePrio" -> each(hashes) { engine.toggleFirstLast(it) }
            "torrents/removeTags" -> each(hashes) { engine.removeTags(it, tags(p["tags"])) }
            "torrents/delete" -> each(hashes) { engine.delete(it, p["deleteFiles"] == "true") }
            else -> notFound()
        }
    }

    private fun add(p: Map<String, String>, files: Map<String, String>): Response {
        val savePath = p["savepath"].orEmpty()
        val category = p["category"].orEmpty()
        val tags = tags(p["tags"])
        val stopped = p["stopped"] == "true" || p["paused"] == "true"
        var added = 0
        var existed = 0
        files["torrents"]?.let { tmp ->
            if (engine.addTorrent(File(tmp).readBytes(), savePath, category, tags, stopped)) added++ else existed++
        }
        for (uri in p["urls"].orEmpty().split('\n').map { it.trim() }.filter { it.startsWith("magnet:") }) {
            val stopOnMetadata = p["stopCondition"] == "MetadataReceived"
            if (engine.addMagnet(uri, savePath, category, tags, stopOnMetadata)) added++ else existed++
        }
        return when {
            added > 0 -> text(Response.Status.OK, "Ok.")
            existed > 0 -> text(Response.Status.CONFLICT, "Torrent is already present")
            else -> text(Response.Status.BAD_REQUEST, "Fails.")
        }
    }

    private fun tags(s: String?): Set<String> = s.orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() }.toSet()

    private inline fun each(hashes: List<String>, fn: (String) -> Unit): Response {
        for (h in hashes) fn(h)
        return ok()
    }

    private fun ok() = text(Response.Status.OK, "")
    private fun notFound() = text(Response.Status.NOT_FOUND, "Not Found")
    private fun text(status: Response.Status, body: String) = newFixedLengthResponse(status, "text/plain; charset=UTF-8", body)
    private fun json(body: String) = newFixedLengthResponse(Response.Status.OK, "application/json", body)
}
