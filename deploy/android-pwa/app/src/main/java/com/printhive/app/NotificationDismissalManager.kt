package com.printhive.app

import android.content.Context
import android.util.Log

/**
 * Tracks and persists user-dismissed notifications across polling cycles and app restarts.
 * Ensures notifications dismissed by the user do not loop/reappear every polling tick.
 */
object NotificationDismissalManager {
    private const val TAG = "PrintHive/Dismissal"
    private const val PREFS_NAME = "printhive_notification_dismissals"
    private const val KEY_DISMISSED_TAGS = "dismissed_tags"

    fun isDismissed(context: Context, printerId: Int, jobTag: String?): Boolean {
        if (jobTag.isNullOrBlank()) return false
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val set = prefs.getStringSet(KEY_DISMISSED_TAGS, emptySet()) ?: emptySet()
        val key = "p${printerId}_$jobTag"
        val isD = set.contains(key) || set.contains("p${printerId}_all_finished")
        if (isD) {
            Log.d(TAG, "Notification for printer $printerId, tag '$jobTag' is flagged as DISMISSED")
        }
        return isD
    }

    fun markDismissed(context: Context, printerId: Int, jobTag: String) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val set = prefs.getStringSet(KEY_DISMISSED_TAGS, emptySet())?.toMutableSet() ?: mutableSetOf()
        val key = "p${printerId}_$jobTag"
        set.add(key)
        prefs.edit().putStringSet(KEY_DISMISSED_TAGS, set).apply()
        Log.d(TAG, "Marked printer $printerId tag '$jobTag' as dismissed")
    }

    fun clearForPrinter(context: Context, printerId: Int) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val set = prefs.getStringSet(KEY_DISMISSED_TAGS, emptySet())?.toMutableSet() ?: mutableSetOf()
        val before = set.size
        set.removeAll { it.startsWith("p${printerId}_") }
        if (set.size != before) {
            prefs.edit().putStringSet(KEY_DISMISSED_TAGS, set).apply()
            Log.d(TAG, "Cleared dismissal tags for printer $printerId ($before -> ${set.size})")
        }
    }
}
