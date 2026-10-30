package com.bridgebot.livemobilelty

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.DisplayMetrics
import android.view.WindowManager
import java.io.ByteArrayOutputStream

/** JPEG screen frames via MediaProjection. Caller owns start/stop. */
class ScreenCapturer(private val ctx: Context) {
    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var reader: ImageReader? = null
    private var callback: MediaProjection.Callback? = null
    var screenW = 0; var screenH = 0; private set

    /** True once at least one real image has been pulled off the VirtualDisplay. */
    @Volatile var everProduced = false; private set
    // Cache of the last encoded frame so a *static* screen (ImageReader delivers no new
    // image until pixels change) keeps streaming instead of being mistaken for "no frames".
    private var lastJpeg: ByteArray? = null
    private var lastBlack = false

    /** One grabbed frame. `fresh`=a new image was available; `black`=looks like a blank/secure screen. */
    data class Frame(val jpg: ByteArray, val fresh: Boolean, val black: Boolean)

    fun start(resultCode: Int, data: Intent, onStopped: (() -> Unit)? = null) {
        stop()
        val mpm = ctx.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val proj = mpm.getMediaProjection(resultCode, data)
            ?: throw IllegalStateException("MediaProjection null — re-allow Screen capture in the app")
        projection = proj
        // Android 14+ (API 34): must register a Callback BEFORE createVirtualDisplay or it throws.
        val cb = object : MediaProjection.Callback() {
            override fun onStop() {
                Handler(Looper.getMainLooper()).post {
                    try { display?.release() } catch (_: Exception) {}
                    try { reader?.close() } catch (_: Exception) {}
                    display = null; reader = null; projection = null
                    onStopped?.invoke()
                }
            }
        }
        callback = cb
        proj.registerCallback(cb, Handler(Looper.getMainLooper()))
        val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getRealMetrics(metrics)
        screenW = metrics.widthPixels
        screenH = metrics.heightPixels
        val density = metrics.densityDpi
        everProduced = false
        lastJpeg = null
        lastBlack = false
        reader = ImageReader.newInstance(screenW, screenH, PixelFormat.RGBA_8888, 2)
        display = proj.createVirtualDisplay(
            "LiveMobilelty", screenW, screenH, density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader!!.surface, null, null
        )
    }

    /**
     * Returns the newest frame, or the cached last frame when the ImageReader has nothing new
     * (static screen), or null when we have never produced any image yet.
     */
    fun grab(maxWidth: Int, quality: Int): Frame? {
        val img = reader?.acquireLatestImage()
        if (img == null) {
            val lj = lastJpeg ?: return null
            return Frame(lj, false, lastBlack)   // resend last good frame for static screens
        }
        try {
            val plane = img.planes[0]
            val buf = plane.buffer
            val pixelStride = plane.pixelStride
            val rowStride = plane.rowStride
            val rowPadding = rowStride - pixelStride * screenW
            val bmp = Bitmap.createBitmap(screenW + rowPadding / pixelStride, screenH, Bitmap.Config.ARGB_8888)
            bmp.copyPixelsFromBuffer(buf)
            var out = Bitmap.createBitmap(bmp, 0, 0, screenW, screenH)
            bmp.recycle()
            val black = isMostlyBlack(out)
            if (out.width > maxWidth) {
                val h = (out.height * (maxWidth.toFloat() / out.width)).toInt().coerceAtLeast(1)
                val scaled = Bitmap.createScaledBitmap(out, maxWidth, h, true)
                out.recycle(); out = scaled
            }
            val baos = ByteArrayOutputStream()
            out.compress(Bitmap.CompressFormat.JPEG, quality.coerceIn(10, 90), baos)
            out.recycle()
            val bytes = baos.toByteArray()
            everProduced = true
            lastJpeg = bytes
            lastBlack = black
            return Frame(bytes, true, black)
        } finally {
            img.close()
        }
    }

    /** Cheap blank/secure-screen heuristic: sample a 7x7 grid; true if every sample is near-black. */
    private fun isMostlyBlack(bmp: Bitmap): Boolean {
        val w = bmp.width; val h = bmp.height
        if (w < 2 || h < 2) return false
        var n = 0
        for (i in 1..7) for (j in 1..7) {
            val x = (w * i / 8).coerceIn(0, w - 1)
            val y = (h * j / 8).coerceIn(0, h - 1)
            val p = bmp.getPixel(x, y)
            if (Color.red(p) > 10 || Color.green(p) > 10 || Color.blue(p) > 10) return false
            n++
        }
        return n > 0
    }

    fun stop() {
        val proj = projection
        val cb = callback
        try { display?.release() } catch (_: Exception) {}
        try { reader?.close() } catch (_: Exception) {}
        if (proj != null && cb != null) {
            try { proj.unregisterCallback(cb) } catch (_: Exception) {}
        }
        try { proj?.stop() } catch (_: Exception) {}
        display = null; reader = null; projection = null; callback = null
        lastJpeg = null; lastBlack = false; everProduced = false
    }

    companion object {
        fun b64(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)

        /** A solid placeholder JPEG with centered text, shown before the first real frame arrives. */
        fun placeholderJpeg(text: String, w: Int = 540, h: Int = 1140): ByteArray {
            val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
            val c = Canvas(bmp)
            c.drawColor(Color.rgb(15, 23, 42))
            val p = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.rgb(203, 213, 225)
                textSize = w / 22f
                textAlign = Paint.Align.CENTER
            }
            var y = h / 2f - (text.split("\n").size - 1) * p.textSize / 2f
            for (line in text.split("\n")) { c.drawText(line, w / 2f, y, p); y += p.textSize * 1.4f }
            val baos = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, 70, baos)
            bmp.recycle()
            return baos.toByteArray()
        }
    }
}
