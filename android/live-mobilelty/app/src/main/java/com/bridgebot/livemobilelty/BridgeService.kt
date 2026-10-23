package com.bridgebot.livemobilelty

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.*
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/**
 * Foreground service: keeps the hub WebSocket alive, streams JPEG frames while a viewer is
 * connected, and applies RD_INPUT gestures via the AccessibilityService.
 */
class BridgeService : Service() {
    companion object {
        const val CH = "live_mobilelty"
        const val NOTIF = 42
        const val ACTION_STOP = "com.bridgebot.livemobilelty.STOP"
        const val EXTRA_RESULT_CODE = "rc"
        const val EXTRA_RESULT_DATA = "rd"
        @Volatile var running = false
        @Volatile var statusText = "Offline"
        var captureResultCode: Int = 0
        var captureData: Intent? = null
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var hub: HubClient? = null
    private var capturer: ScreenCapturer? = null
    private val viewers = ConcurrentHashMap<String, Viewer>()
    private var frameJob: Job? = null
    private var wake: PowerManager.WakeLock? = null

    data class Viewer(var quality: Int, var width: Int, var fps: Float, var inflight: Int = 0, var last: Long = System.currentTimeMillis())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startAsForeground(getString(R.string.notif_body))
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "livemobilelty:hub").also { it.acquire(6 * 60 * 60 * 1000L) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelfSafe(); return START_NOT_STICKY
        }
        if (intent?.hasExtra(EXTRA_RESULT_CODE) == true) {
            captureResultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
            @Suppress("DEPRECATION")
            captureData = intent.getParcelableExtra(EXTRA_RESULT_DATA)
            ensureCapturer()
        }
        if (hub == null) connectHub()
        running = true
        return START_STICKY
    }

    private fun connectHub() {
        val prefs = Prefs(this)
        if (prefs.apiKey.isBlank()) {
            setStatus("No key — open the app and pair")
            return
        }
        hub?.stop()
        hub = HubClient(prefs.hub, prefs.apiKey, prefs.deviceName, scope,
            onStatus = { setStatus(it) },
            onMessage = { handle(it) }
        ).also { it.start() }
    }

    private fun handle(m: JSONObject) {
        val t = m.optString("type").uppercase()
        if (!t.startsWith("RD_")) return
        val viewer = m.optString("viewer")
        if (viewer.isBlank()) return
        when (t) {
            "RD_STOP" -> { viewers.remove(viewer); if (viewers.isEmpty()) stopFrames() }
            "RD_START" -> {
                viewers[viewer] = Viewer(
                    quality = m.optInt("quality", 55).coerceIn(10, 90),
                    width = m.optInt("width", 720).coerceIn(320, 1440),
                    fps = m.optDouble("fps", 5.0).toFloat().coerceIn(0.5f, 12f),
                )
                setStatus(getString(R.string.notif_session))
                ensureCapturer()
                startFrames()
            }
            "RD_ACK" -> viewers[viewer]?.let { it.inflight = (it.inflight - 1).coerceAtLeast(0); it.last = System.currentTimeMillis() }
            "RD_INPUT" -> applyInput(m)
            else -> { /* ignore clip/files on phone for v1 */ }
        }
    }

    private fun applyInput(m: JSONObject) {
        val a11y = BridgeAccessibilityService.instance
        if (a11y == null) {
            reply(m.optString("viewer"), JSONObject().put("type", "RD_ERROR").put("error", "Enable the Accessibility service in the app"))
            return
        }
        val sw = (capturer?.screenW ?: resources.displayMetrics.widthPixels).toFloat()
        val sh = (capturer?.screenH ?: resources.displayMetrics.heightPixels).toFloat()
        val act = m.optString("action")
        when (act) {
            "tap", "click" -> {
                val x = (m.optDouble("x") * sw).toFloat()
                val y = (m.optDouble("y") * sh).toFloat()
                a11y.tap(x, y)
            }
            "swipe" -> {
                val x1 = (m.optDouble("x1", m.optDouble("x")) * sw).toFloat()
                val y1 = (m.optDouble("y1", m.optDouble("y")) * sh).toFloat()
                val x2 = (m.optDouble("x2") * sw).toFloat()
                val y2 = (m.optDouble("y2") * sh).toFloat()
                a11y.swipe(x1, y1, x2, y2, m.optLong("duration_ms", 300))
            }
            "type" -> a11y.typeText(m.optString("text").take(2000))
            "back" -> a11y.global(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK)
            "home" -> a11y.global(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
            "recents" -> a11y.global(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_RECENTS)
            "key" -> when (m.optString("keys").lowercase()) {
                "back", "escape" -> a11y.global(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK)
                "home" -> a11y.global(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
                "recents" -> a11y.global(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_RECENTS)
            }
        }
    }

    private fun ensureCapturer() {
        val data = captureData ?: return
        if (capturer == null) {
            capturer = ScreenCapturer(this).also {
                try { it.start(captureResultCode, data) }
                catch (e: Exception) { Log.e("LiveMobilelty", "capture start", e); capturer = null }
            }
        }
    }

    private fun startFrames() {
        if (frameJob?.isActive == true) return
        frameJob = scope.launch(Dispatchers.Default) {
            while (isActive && viewers.isNotEmpty()) {
                val now = System.currentTimeMillis()
                viewers.entries.removeIf { now - it.value.last > 30_000 }
                val ready = viewers.filter { it.value.inflight < 2 }
                if (ready.isEmpty()) { delay(40); continue }
                val cap = capturer
                if (cap == null) {
                    ready.keys.forEach { reply(it, JSONObject().put("type", "RD_ERROR").put("error", "Allow screen capture in the app")) }
                    delay(1000); continue
                }
                val v = ready.values.maxBy { it.fps }
                val t0 = System.currentTimeMillis()
                val jpg = try { cap.grabJpeg(v.width, v.quality) } catch (e: Exception) { null }
                if (jpg != null) {
                    val b64 = ScreenCapturer.b64(jpg)
                    for ((vid, vv) in ready) {
                        vv.inflight++
                        vv.last = now
                        reply(vid, JSONObject()
                            .put("type", "RD_FRAME")
                            .put("jpg", b64)
                            .put("sw", cap.screenW)
                            .put("sh", cap.screenH))
                    }
                }
                val wait = (1000f / v.fps - (System.currentTimeMillis() - t0)).toLong().coerceAtLeast(0)
                delay(wait)
            }
        }
    }

    private fun stopFrames() { frameJob?.cancel(); frameJob = null }

    private fun reply(viewer: String, obj: JSONObject) {
        obj.put("viewer", viewer)
        hub?.send(obj)
    }

    private fun setStatus(s: String) {
        statusText = s
        startAsForeground(s)
        sendBroadcast(Intent("com.bridgebot.livemobilelty.STATUS").putExtra("status", s))
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH, getString(R.string.notif_channel), NotificationManager.IMPORTANCE_LOW))
        }
    }

    private fun startAsForeground(body: String) {
        val stop = PendingIntent.getService(
            this, 0, Intent(this, BridgeService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val n = NotificationCompat.Builder(this, CH)
            .setContentTitle(getString(R.string.notif_title))
            .setContentText(body)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(0, "Stop", stop)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIF, n)
        }
    }

    private fun stopSelfSafe() {
        running = false
        hub?.stop(); hub = null
        stopFrames()
        capturer?.stop(); capturer = null
        try { wake?.release() } catch (_: Exception) {}
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopSelfSafe()
        scope.cancel()
        super.onDestroy()
    }
}
