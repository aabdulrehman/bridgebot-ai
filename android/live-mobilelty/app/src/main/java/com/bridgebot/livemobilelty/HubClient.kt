package com.bridgebot.livemobilelty

import android.util.Log
import kotlinx.coroutines.*
import okhttp3.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min

/**
 * Outbound WebSocket to BridgeBot /ws/connect. Speaks the same RD_* protocol as the desktop connector
 * so the existing owner-only dashboard relay works unchanged.
 */
class HubClient(
    private val hub: String,
    private val apiKey: String,
    private val deviceName: String,
    private val scope: CoroutineScope,
    private val onStatus: (String) -> Unit,
    private val onMessage: (JSONObject) -> Unit,
) {
    private val TAG = "LiveMobilelty"
    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private var ws: WebSocket? = null
    private val running = AtomicBoolean(false)
    private var attempt = 0
    private var streamId = java.util.UUID.randomUUID().toString().replace("-", "").take(16)

    fun start() {
        if (!running.compareAndSet(false, true)) return
        connect()
    }

    fun stop() {
        running.set(false)
        ws?.close(1000, "stop")
        ws = null
        onStatus("Stopped")
    }

    fun send(obj: JSONObject) {
        ws?.send(obj.toString())
    }

    private fun connect() {
        if (!running.get()) return
        onStatus("Connecting…")
        val url = hub.replace("https://", "wss://").replace("http://", "ws://") +
            "/ws/connect?api_key=" + java.net.URLEncoder.encode(apiKey, "UTF-8")
        val req = Request.Builder().url(url).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                attempt = 0
                onStatus("Online · waiting for laptop")
                val hello = JSONObject()
                    .put("type", "HELLO")
                    .put("machine", deviceName)
                    .put("version", "android-1.0.1")
                    .put("platform", "Android ${android.os.Build.VERSION.RELEASE}")
                    .put("agents", JSONArray())
                    .put("features", JSONArray().put("android").put("remote_desktop"))
                    .put("stream", streamId)
                webSocket.send(hello.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val m = JSONObject(text)
                    val t = m.optString("type").uppercase()
                    if (t == "WELCOME") {
                        onStatus("Online · paired")
                    } else if (t == "PONG" || t == "ACK") {
                        /* noop */
                    } else if (t == "GOODBYE") {
                        onStatus("Kicked: ${m.optString("reason")}")
                    } else {
                        onMessage(m)
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "bad message", e)
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                scheduleReconnect(code, reason)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "ws failure", t)
                scheduleReconnect(0, t.message ?: "network")
            }
        })
        // keepalive PING matching the desktop connector
        scope.launch {
            while (running.get() && ws != null) {
                delay(25_000)
                ws?.send(JSONObject().put("type", "PING").put("ts", System.currentTimeMillis()).toString())
            }
        }
    }

    private fun scheduleReconnect(code: Int, reason: String) {
        if (!running.get()) return
        val authFail = code in listOf(4401, 4403, 4001)
        val delayMs = if (authFail) {
            onStatus("Auth failed ($code) — check key. Retry in 60s")
            60_000L
        } else {
            val d = min(30_000L, 1_000L * (1L shl min(attempt, 5)))
            attempt++
            onStatus("Reconnecting in ${d / 1000}s… ($reason)")
            d
        }
        scope.launch {
            delay(delayMs)
            if (running.get()) connect()
        }
    }
}
