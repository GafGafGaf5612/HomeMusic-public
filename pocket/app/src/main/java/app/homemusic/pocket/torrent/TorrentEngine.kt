package app.homemusic.pocket.torrent

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import org.libtorrent4j.AddTorrentParams
import org.libtorrent4j.AlertListener
import org.libtorrent4j.Priority
import org.libtorrent4j.SessionHandle
import org.libtorrent4j.SessionManager
import org.libtorrent4j.SessionParams
import org.libtorrent4j.SettingsPack
import org.libtorrent4j.Sha1Hash
import org.libtorrent4j.TorrentFlags
import org.libtorrent4j.TorrentHandle
import org.libtorrent4j.TorrentInfo
import org.libtorrent4j.TorrentStatus
import org.libtorrent4j.Vectors
import org.libtorrent4j.alerts.Alert
import org.libtorrent4j.alerts.AlertType
import org.libtorrent4j.alerts.MetadataReceivedAlert
import org.libtorrent4j.alerts.SaveResumeDataAlert
import org.libtorrent4j.alerts.TorrentAlert
import org.libtorrent4j.swig.error_code
import org.libtorrent4j.swig.libtorrent
import org.libtorrent4j.swig.load_torrent_limits
import org.libtorrent4j.swig.remove_flags_t
import org.libtorrent4j.swig.torrent_flags_t
import java.io.File
import java.util.concurrent.ConcurrentHashMap

/**
 * The torrent client of the phone app: libtorrent (libtorrent4j) with what the Hub asks of qBittorrent
 * (hub/app/torrents/qbit.py) — torrents added stopped, magnets stopped once their file list arrives, file priorities,
 * sequential download with the first and last pieces first (listening before downloading), piece states, tags.
 * [QbitServer] serves it over qBittorrent's Web API, so the Hub runs unchanged.
 *
 * State survives restarts: `<hash>.json` (what qBittorrent would keep: category, tags, added time, flags), libtorrent's
 * resume data `<hash>.resume` (with the info dict), or the magnet while there's no metadata yet.
 */
class TorrentEngine(private val stateDir: File) {
    private val tag = "PocketTorrents"
    private val session = SessionManager(false)
    private val metas = ConcurrentHashMap<String, Meta>()

    class Meta(
        val hash: String,
        var savePath: String,
        var category: String,
        val tags: MutableSet<String>,
        val addedOn: Long,
        var magnet: String? = null,
        var stopOnMetadata: Boolean = false,
        var firstLast: Boolean = false,
        var lastActivity: Long = addedOn,
    ) {
        fun toJson(): JSONObject = JSONObject()
            .put("hash", hash).put("save_path", savePath).put("category", category).put("tags", JSONArray(tags.toList()))
            .put("added_on", addedOn).put("magnet", magnet).put("stop_on_metadata", stopOnMetadata)
            .put("first_last", firstLast).put("last_activity", lastActivity)

        companion object {
            fun fromJson(o: JSONObject): Meta {
                val tags = mutableSetOf<String>()
                o.optJSONArray("tags")?.let { a -> for (i in 0 until a.length()) tags += a.getString(i) }
                return Meta(
                    o.getString("hash"), o.getString("save_path"), o.optString("category"), tags, o.getLong("added_on"),
                    o.optString("magnet").takeIf { it.isNotEmpty() && it != "null" }, o.optBoolean("stop_on_metadata"),
                    o.optBoolean("first_last"), o.optLong("last_activity", o.getLong("added_on")),
                )
            }
        }
    }

    fun start() {
        stateDir.mkdirs()
        val sp = SettingsPack()
        sp.listenInterfaces("0.0.0.0:6881,[::]:6881")
        sp.setEnableDht(true)
        sp.setDhtBootstrapNodes(
            "dht.libtorrent.org:25401,router.bittorrent.com:6881,router.utorrent.com:6881,dht.transmissionbt.com:6881"
        )
        session.addListener(object : AlertListener {
            override fun types(): IntArray = intArrayOf(
                AlertType.METADATA_RECEIVED.swig(), AlertType.SAVE_RESUME_DATA.swig(), AlertType.TORRENT_FINISHED.swig(),
                AlertType.TORRENT_ERROR.swig(), AlertType.FILE_ERROR.swig(), AlertType.LISTEN_FAILED.swig(),
                AlertType.LISTEN_SUCCEEDED.swig(), AlertType.DHT_ERROR.swig(), AlertType.DHT_BOOTSTRAP.swig(),
            )

            override fun alert(alert: Alert<*>) = onAlert(alert)
        })
        session.start(SessionParams(sp))
        restore()
    }

    fun stop() {
        for (h in handles()) if (h.isValid) h.saveResumeData(TorrentHandle.SAVE_INFO_DICT)
        Thread.sleep(800) // the resume data alerts come back asynchronously
        session.stop()
    }

    // ------------------------------------------------------------------------------------------------ adding

    /** Returns false when the torrent is there already (qBittorrent 5 answers 409). */
    @Synchronized
    fun addTorrent(data: ByteArray, savePath: String, category: String, tags: Set<String>, stopped: Boolean): Boolean {
        val ti = TorrentInfo(data)
        val hash = ti.infoHashes().v1.toHex().lowercase()
        if (find(hash) != null) return false
        val p = AddTorrentParams()
        p.setTorrentInfo(ti)
        p.setSavePath(savePath)
        p.setFlags(if (stopped) TorrentFlags.PAUSED else torrent_flags_t())
        val meta = Meta(hash, savePath, category, tags.toMutableSet(), nowS())
        metas[hash] = meta
        add(p)
        File(stateDir, "$hash.torrent").writeBytes(data)
        saveMeta(meta)
        return true
    }

    @Synchronized
    fun addMagnet(uri: String, savePath: String, category: String, tags: Set<String>, stopOnMetadata: Boolean): Boolean {
        val p = AddTorrentParams.parseMagnetUri(uri)
        val hash = p.infoHashes.v1.toHex().lowercase()
        if (find(hash) != null) return false
        p.setSavePath(savePath)
        p.setFlags(torrent_flags_t())
        val meta = Meta(hash, savePath, category, tags.toMutableSet(), nowS(), magnet = uri, stopOnMetadata = stopOnMetadata)
        metas[hash] = meta
        add(p)
        saveMeta(meta)
        return true
    }

    private fun add(p: AddTorrentParams): TorrentHandle {
        val ec = error_code()
        val h = TorrentHandle(session.swig().add_torrent(p.swig(), ec))
        if (ec.value() != 0) throw IllegalArgumentException(ec.message())
        h.unsetFlags(TorrentFlags.AUTO_MANAGED) // started and stopped by the Hub only, never by the queue
        return h
    }

    private fun restore() {
        val files = stateDir.listFiles { f -> f.name.endsWith(".json") } ?: return
        for (f in files) {
            try {
                val meta = Meta.fromJson(JSONObject(f.readText()))
                metas[meta.hash] = meta
                val resume = File(stateDir, "${meta.hash}.resume")
                val torrent = File(stateDir, "${meta.hash}.torrent")
                when {
                    resume.isFile -> {
                        val ec = error_code()
                        val atp = libtorrent.read_resume_data_ex(Vectors.bytes2byte_vector(resume.readBytes()), ec, load_torrent_limits())
                        if (ec.value() != 0) throw IllegalStateException(ec.message())
                        add(AddTorrentParams(atp))
                    }
                    torrent.isFile -> {
                        val p = AddTorrentParams()
                        p.setTorrentInfo(TorrentInfo(torrent.readBytes()))
                        p.setSavePath(meta.savePath)
                        p.setFlags(TorrentFlags.PAUSED)
                        add(p)
                    }
                    meta.magnet != null -> {
                        val p = AddTorrentParams.parseMagnetUri(meta.magnet)
                        p.setSavePath(meta.savePath)
                        add(p)
                    }
                    else -> forget(meta.hash)
                }
            } catch (e: Exception) {
                app.homemusic.pocket.PocketLog.w(tag, "can't restore ${f.name}", e)
            }
        }
    }

    // ------------------------------------------------------------------------------------------------ alerts

    private fun onAlert(alert: Alert<*>) {
        try {
            when (alert) {
                is MetadataReceivedAlert -> {
                    val h = alert.handle()
                    val meta = metas[hashOf(h)] ?: return
                    if (meta.stopOnMetadata) { // qBittorrent's stopCondition=MetadataReceived
                        h.pause()
                        meta.stopOnMetadata = false
                    }
                    export(meta.hash)?.let { File(stateDir, "${meta.hash}.torrent").writeBytes(it) }
                    saveMeta(meta)
                }
                is SaveResumeDataAlert -> {
                    val h = alert.handle()
                    val hash = hashOf(h)
                    if (metas.containsKey(hash)) {
                        File(stateDir, "$hash.resume").writeBytes(AddTorrentParams.writeResumeDataBuf(alert.params()))
                    }
                }
                is TorrentAlert<*> -> {
                    if (alert.type() == AlertType.TORRENT_FINISHED) alert.handle().saveResumeData(TorrentHandle.SAVE_INFO_DICT)
                    else app.homemusic.pocket.PocketLog.w(tag, "${alert.type()}: ${alert.message()}")
                }
                else -> app.homemusic.pocket.PocketLog.i(tag, "${alert.type()}: ${alert.message()}")
            }
        } catch (e: Exception) {
            app.homemusic.pocket.PocketLog.w(tag, "alert ${alert.type()}", e)
        }
    }

    /** Called every few seconds: resume data of what changed (a phone app can die at any moment). */
    fun saveChanged() {
        for (h in handles()) if (h.isValid && h.needSaveResumeData()) h.saveResumeData(TorrentHandle.SAVE_INFO_DICT)
    }

    // ------------------------------------------------------------------------------------------------ queries

    private fun handles(): List<TorrentHandle> = metas.keys.mapNotNull { find(it) }

    fun find(hash: String): TorrentHandle? =
        try { session.find(Sha1Hash.parseHex(hash.lowercase())) } catch (e: Exception) { null }

    private fun hashOf(h: TorrentHandle): String = h.status().infoHashes.v1.toHex().lowercase()

    fun exists(hash: String) = metas.containsKey(hash.lowercase()) && find(hash) != null

    fun info(hashes: Set<String>?, category: String?): JSONArray {
        val out = JSONArray()
        for ((hash, meta) in metas) {
            if (hashes != null && hash !in hashes) continue
            if (category != null && meta.category != category) continue
            val h = find(hash) ?: continue
            out.put(torrentJson(h, meta))
        }
        return out
    }

    private fun torrentJson(h: TorrentHandle, meta: Meta): JSONObject {
        val st = h.status()
        val flags = st.flags()
        val paused = flags.and_(TorrentFlags.PAUSED).non_zero()
        val seq = flags.and_(TorrentFlags.SEQUENTIAL_DOWNLOAD).non_zero()
        val wanted = st.totalWanted()
        val done = st.totalWantedDone()
        val left = (wanted - done).coerceAtLeast(0)
        val dl = st.downloadPayloadRate()
        val up = st.uploadPayloadRate()
        if (dl > 0 || up > 0) meta.lastActivity = nowS()
        val progress = if (!st.hasMetadata()) 0.0 else if (wanted > 0) done.toDouble() / wanted else 1.0
        val complete = st.hasMetadata() && left == 0L
        val state = when {
            st.errorCode().isError -> "error"
            !st.hasMetadata() -> if (paused) "stoppedDL" else "metaDL"
            st.state() == TorrentStatus.State.CHECKING_FILES || st.state() == TorrentStatus.State.CHECKING_RESUME_DATA ->
                if (complete) "checkingUP" else "checkingDL"
            paused -> if (complete) "stoppedUP" else "stoppedDL"
            complete -> if (up > 0) "uploading" else "stalledUP"
            dl > 0 -> "downloading"
            else -> "stalledDL"
        }
        val ti = if (st.hasMetadata()) h.torrentFile() else null
        val name = ti?.name() ?: st.name().ifEmpty { meta.hash }
        return JSONObject()
            .put("hash", meta.hash).put("infohash_v1", meta.hash).put("name", name)
            .put("size", wanted).put("total_size", ti?.totalSize() ?: 0)
            .put("progress", progress).put("amount_left", left).put("completed", done)
            .put("dlspeed", dl).put("upspeed", up)
            .put("num_seeds", st.numSeeds()).put("num_leechs", (st.numPeers() - st.numSeeds()).coerceAtLeast(0))
            .put("num_complete", st.numComplete()).put("num_incomplete", st.numIncomplete())
            .put("eta", if (dl > 0) left / dl else 8640000)
            .put("state", state).put("category", meta.category).put("tags", meta.tags.sorted().joinToString(", "))
            .put("save_path", meta.savePath).put("content_path", File(meta.savePath, name).path)
            .put("added_on", meta.addedOn).put("last_activity", meta.lastActivity)
            .put("seq_dl", seq).put("f_l_piece_prio", meta.firstLast)
            .put("auto_tmm", false).put("magnet_uri", meta.magnet ?: h.makeMagnetUri())
    }

    /** null: no such torrent; empty: no metadata yet (a magnet). */
    fun files(hash: String): JSONArray? {
        val h = find(hash) ?: return null
        val out = JSONArray()
        if (!h.status().hasMetadata()) return out
        val ti = h.torrentFile()
        val fs = ti.files()
        val progress = h.fileProgress(TorrentHandle.PIECE_GRANULARITY)
        val prios = h.filePriorities()
        for (i in 0 until fs.numFiles()) {
            if (fs.padFileAt(i)) continue
            val size = fs.fileSize(i)
            out.put(JSONObject()
                .put("index", i).put("name", fs.filePath(i)).put("size", size)
                .put("progress", if (size > 0) progress[i].toDouble() / size else 1.0)
                .put("priority", prios[i].swig().toInt())
                .put("piece_range", JSONArray(listOf(fs.pieceIndexAtFile(i), fs.lastPieceIndexAtFile(i))))
                .put("is_seed", size > 0 && progress[i] == size))
        }
        return out
    }

    fun properties(hash: String): JSONObject? {
        val h = find(hash) ?: return null
        val st = h.status()
        val ti = if (st.hasMetadata()) h.torrentFile() else null
        return JSONObject().put("piece_size", ti?.pieceLength() ?: 0).put("pieces_num", ti?.numPieces() ?: 0)
            .put("save_path", metas[hash]?.savePath).put("total_size", ti?.totalSize() ?: 0)
    }

    fun export(hash: String): ByteArray? {
        val h = find(hash) ?: return null
        if (!h.status().hasMetadata()) return null
        val p = AddTorrentParams()
        p.setTorrentInfo(h.torrentFile())
        return Vectors.byte_vector2bytes(libtorrent.write_torrent_file_buf_ex(p.swig()))
    }

    /** Per piece: 0 not downloaded, 1 downloading, 2 downloaded (qBittorrent's torrents/pieceStates). */
    fun pieceStates(hash: String): IntArray? {
        val h = find(hash) ?: return null
        val st = h.status(TorrentHandle.QUERY_PIECES)
        if (!st.hasMetadata()) return IntArray(0)
        val pieces = st.pieces()
        val out = IntArray(pieces.size()) { if (pieces.getBit(it)) 2 else 0 }
        for (p in h.downloadQueue) if (p.pieceIndex() in out.indices && out[p.pieceIndex()] == 0) out[p.pieceIndex()] = 1
        return out
    }

    // ------------------------------------------------------------------------------------------------ changes

    fun setFilePriority(hash: String, ids: List<Int>, priority: Int) {
        val h = find(hash) ?: return
        val prio = Priority.fromSwig(priority.coerceIn(0, 7))
        for (i in ids) h.filePriority(i, prio)
        metas[hash]?.let { touch(it); if (it.firstLast) applyFirstLast(h) }
    }

    fun start(hash: String) {
        val h = find(hash) ?: return
        h.unsetFlags(TorrentFlags.AUTO_MANAGED)
        h.resume()
        metas[hash]?.let { it.stopOnMetadata = false; touch(it) }
    }

    fun stopTorrent(hash: String) {
        val h = find(hash) ?: return
        h.unsetFlags(TorrentFlags.AUTO_MANAGED)
        h.pause()
        h.saveResumeData(TorrentHandle.SAVE_INFO_DICT)
    }

    fun toggleSequential(hash: String) {
        val h = find(hash) ?: return
        if (h.status().flags().and_(TorrentFlags.SEQUENTIAL_DOWNLOAD).non_zero()) h.unsetFlags(TorrentFlags.SEQUENTIAL_DOWNLOAD)
        else h.setFlags(TorrentFlags.SEQUENTIAL_DOWNLOAD)
        metas[hash]?.let { touch(it) }
    }

    fun toggleFirstLast(hash: String) {
        val h = find(hash) ?: return
        val meta = metas[hash] ?: return
        meta.firstLast = !meta.firstLast
        if (meta.firstLast) applyFirstLast(h) else h.prioritizeFiles(h.filePriorities()) // resets piece priorities
        touch(meta)
    }

    /** The first and the last piece of every wanted file at the top (players read a file's ends first). */
    private fun applyFirstLast(h: TorrentHandle) {
        if (!h.status().hasMetadata()) return
        val fs = h.torrentFile().files()
        val prios = h.filePriorities()
        for (i in 0 until fs.numFiles()) {
            if (fs.padFileAt(i) || prios[i] == Priority.IGNORE || fs.fileSize(i) == 0L) continue
            h.piecePriority(fs.pieceIndexAtFile(i), Priority.TOP_PRIORITY)
            h.piecePriority(fs.lastPieceIndexAtFile(i), Priority.TOP_PRIORITY)
        }
    }

    fun removeTags(hash: String, tags: Set<String>) {
        val meta = metas[hash] ?: return
        if (tags.isEmpty()) meta.tags.clear() else meta.tags.removeAll(tags)
        saveMeta(meta)
    }

    fun delete(hash: String, deleteFiles: Boolean) {
        val h = find(hash)
        if (h != null) session.remove(h, if (deleteFiles) SessionHandle.DELETE_FILES else remove_flags_t())
        forget(hash)
    }

    private fun forget(hash: String) {
        metas.remove(hash)
        for (ext in listOf("json", "resume", "torrent")) File(stateDir, "$hash.$ext").delete()
    }

    private fun touch(meta: Meta) {
        meta.lastActivity = nowS()
        saveMeta(meta)
    }

    private fun saveMeta(meta: Meta) {
        val f = File(stateDir, "${meta.hash}.json")
        val tmp = File(stateDir, "${meta.hash}.json.tmp")
        tmp.writeText(meta.toJson().toString())
        tmp.renameTo(f)
    }

    private fun nowS() = System.currentTimeMillis() / 1000

    /** qBittorrent's transfer/info: the session as a whole (DHT nodes tell whether magnets can find peers). */
    fun transferInfo(): JSONObject = JSONObject()
        .put("dl_info_speed", session.downloadRate()).put("up_info_speed", session.uploadRate())
        .put("dht_nodes", session.dhtNodes())
        .put("connection_status", if (!session.isRunning) "disconnected" else if (session.isFirewalled) "firewalled" else "connected")
        .put("listen", JSONArray(session.listenEndpoints()))

    /** For the notification: torrents downloading right now. */
    fun activeCount(): Int = handles().count { h ->
        val st = h.status()
        !st.flags().and_(TorrentFlags.PAUSED).non_zero() && st.hasMetadata() && st.totalWanted() > st.totalWantedDone()
    }
}
