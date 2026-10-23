package com.bridgebot.livemobilelty

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import kotlinx.coroutines.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var status: TextView
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val rx = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) {
            status.text = i?.getStringExtra("status") ?: BridgeService.statusText
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        status = findViewById(R.id.status)
        val hub = findViewById<EditText>(R.id.hub)
        val code = findViewById<EditText>(R.id.code)
        val key = findViewById<EditText>(R.id.key)
        hub.setText(prefs.hub)
        key.setText(prefs.apiKey)

        if (Build.VERSION.SDK_INT >= 33) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }

        findViewById<Button>(R.id.btnPair).setOnClickListener {
            prefs.hub = hub.text.toString()
            val c = code.text.toString().trim()
            if (c.length != 6) { toast("Enter the 6-digit code from the website"); return@setOnClickListener }
            scope.launch { pair(c) }
        }
        findViewById<Button>(R.id.btnStart).setOnClickListener {
            prefs.hub = hub.text.toString()
            val k = key.text.toString().trim()
            if (k.isNotBlank()) prefs.apiKey = k
            if (prefs.apiKey.isBlank()) { toast("Pair with a code or paste a bbk_ key first"); return@setOnClickListener }
            startActivity(Intent(this, CapturePermissionActivity::class.java))
        }
        findViewById<Button>(R.id.btnStop).setOnClickListener {
            startService(Intent(this, BridgeService::class.java).setAction(BridgeService.ACTION_STOP))
            status.text = "Stopped"
        }
        findViewById<Button>(R.id.btnCapture).setOnClickListener {
            startActivity(Intent(this, CapturePermissionActivity::class.java))
        }
        findViewById<Button>(R.id.btnA11y).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        findViewById<Button>(R.id.btnBattery).setOnClickListener {
            try {
                val i = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.parse("package:$packageName"))
                startActivity(i)
            } catch (_: Exception) {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        }
        status.text = if (BridgeService.running) BridgeService.statusText else "Offline"
    }

    private suspend fun pair(code: String) = withContext(Dispatchers.IO) {
        try {
            val body = JSONObject().put("code", code).toString()
                .toRequestBody("application/json".toMediaType())
            val req = Request.Builder().url("${prefs.hub}/api/mobile/pair").post(body).build()
            val resp = OkHttpClient().newCall(req).execute()
            val text = resp.body?.string() ?: ""
            if (!resp.isSuccessful) throw RuntimeException(JSONObject(text).optString("detail", text))
            val apiKey = JSONObject(text).getString("api_key")
            withContext(Dispatchers.Main) {
                prefs.apiKey = apiKey
                findViewById<EditText>(R.id.key).setText(apiKey)
                toast("Paired! Now tap Start and allow screen capture.")
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) { toast("Pair failed: ${e.message}") }
        }
    }

    override fun onResume() {
        super.onResume()
        val f = IntentFilter("com.bridgebot.livemobilelty.STATUS")
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(rx, f, RECEIVER_NOT_EXPORTED)
        else registerReceiver(rx, f)
        status.text = if (BridgeService.running) BridgeService.statusText else status.text
    }

    override fun onPause() {
        try { unregisterReceiver(rx) } catch (_: Exception) {}
        super.onPause()
    }

    override fun onDestroy() { scope.cancel(); super.onDestroy() }
    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()
}
