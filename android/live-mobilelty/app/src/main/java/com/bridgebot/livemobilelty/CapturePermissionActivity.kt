package com.bridgebot.livemobilelty

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle

/** Tiny trampoline that shows the system MediaProjection consent dialog, then starts BridgeService. */
class CapturePermissionActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        @Suppress("DEPRECATION")
        startActivityForResult(mpm.createScreenCaptureIntent(), 7)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == 7 && resultCode == RESULT_OK && data != null) {
            val i = Intent(this, BridgeService::class.java)
                .putExtra(BridgeService.EXTRA_RESULT_CODE, resultCode)
                .putExtra(BridgeService.EXTRA_RESULT_DATA, data)
            startForegroundService(i)
        }
        finish()
    }
}
