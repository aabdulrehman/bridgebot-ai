package com.bridgebot.livemobilelty

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.util.Base64
import android.util.DisplayMetrics
import android.view.WindowManager
import java.io.ByteArrayOutputStream

/** JPEG screen frames via MediaProjection. Caller owns start/stop. */
class ScreenCapturer(private val ctx: Context) {
    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var reader: ImageReader? = null
    var screenW = 0; var screenH = 0; private set

    fun start(resultCode: Int, data: Intent) {
        stop()
        val mpm = ctx.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projection = mpm.getMediaProjection(resultCode, data)
        val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getRealMetrics(metrics)
        screenW = metrics.widthPixels
        screenH = metrics.heightPixels
        val density = metrics.densityDpi
        reader = ImageReader.newInstance(screenW, screenH, PixelFormat.RGBA_8888, 2)
        display = projection!!.createVirtualDisplay(
            "LiveMobilelty", screenW, screenH, density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader!!.surface, null, null
        )
    }

    fun grabJpeg(maxWidth: Int, quality: Int): ByteArray? {
        val img = reader?.acquireLatestImage() ?: return null
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
            if (out.width > maxWidth) {
                val h = (out.height * (maxWidth.toFloat() / out.width)).toInt()
                val scaled = Bitmap.createScaledBitmap(out, maxWidth, h, true)
                out.recycle(); out = scaled
            }
            val baos = ByteArrayOutputStream()
            out.compress(Bitmap.CompressFormat.JPEG, quality.coerceIn(10, 90), baos)
            out.recycle()
            return baos.toByteArray()
        } finally {
            img.close()
        }
    }

    fun stop() {
        try { display?.release() } catch (_: Exception) {}
        try { reader?.close() } catch (_: Exception) {}
        try { projection?.stop() } catch (_: Exception) {}
        display = null; reader = null; projection = null
    }

    companion object {
        fun b64(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)
    }
}
