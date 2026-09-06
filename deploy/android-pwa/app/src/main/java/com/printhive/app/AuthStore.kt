package com.printhive.app

import android.content.Context
import android.util.Log

object AuthStore {
    private const val TAG = "PrintHive/AuthStore"
    private const val PREFS_NAME = "printhive_auth_prefs"
    private const val KEY_AUTH_TOKEN = "auth_token"

    @Volatile
    private var inMemoryToken: String? = null

    fun getToken(context: Context): String? {
        if (!inMemoryToken.isNullOrBlank()) {
            return inMemoryToken
        }
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val saved = prefs.getString(KEY_AUTH_TOKEN, null)
        inMemoryToken = saved
        return saved
    }

    fun setToken(context: Context, token: String?): Boolean {
        val clean = token?.trim('"', ' ', '\'')?.takeIf { it.isNotBlank() && it != "null" }
        val current = getToken(context)
        if (clean == current) {
            return false
        }
        inMemoryToken = clean
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().putString(KEY_AUTH_TOKEN, clean).apply()
        Log.d(TAG, "Auth token updated (present=${!clean.isNullOrBlank()}, length=${clean?.length ?: 0})")
        return true
    }
}
