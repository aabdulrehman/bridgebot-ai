package com.bridgebot.livemobilelty

import android.content.Context

/** Tiny SharedPreferences wrapper for hub URL + connector key. */
class Prefs(ctx: Context) {
    private val p = ctx.getSharedPreferences("live_mobilelty", Context.MODE_PRIVATE)
    var hub: String
        get() = p.getString("hub", BuildConfig.DEFAULT_HUB)!!.trimEnd('/')
        set(v) = p.edit().putString("hub", v.trimEnd('/')).apply()
    var apiKey: String
        get() = p.getString("api_key", "")!!
        set(v) = p.edit().putString("api_key", v).apply()
    var deviceName: String
        get() = p.getString("device_name", "Android phone")!!
        set(v) = p.edit().putString("device_name", v).apply()
}
