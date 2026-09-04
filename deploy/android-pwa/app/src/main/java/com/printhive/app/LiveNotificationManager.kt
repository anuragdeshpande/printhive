package com.printhive.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Build
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.graphics.drawable.IconCompat
import java.util.Locale

data class LivePrintStatus(
    val printerId: Int,
    val printerName: String,
    val state: String, // RUNNING, PAUSE, PREPARE, FINISH, FAILED, IDLE
    val subtaskName: String?,
    val progress: Int, // 0-100
    val remainingSeconds: Int?,
    val layerNum: Int?,
    val totalLayers: Int?,
    val nozzleTemp: Float?,
    val bedTemp: Float?,
    val stageName: String?, // e.g., "Auto bed leveling", "Heatbed preheating"
    val filamentType: String?, // e.g., "PLA Basic"
    val filamentColorHex: String?, // e.g., "#FFFFFF"
    val coverUrl: String?
)

object LiveNotificationManager {

    const val CHANNEL_ID = "printhive_live_activity_v2"
    const val COMPLETION_CHANNEL_ID = "printhive_completion"
    const val NOTIFICATION_ID_BASE = 10000

    fun createNotificationChannels(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

            try {
                manager.deleteNotificationChannel("printhive_live_activity")
            } catch (e: Exception) {}

            // 1. Live Ongoing Channel
            val liveChannel = NotificationChannel(
                CHANNEL_ID,
                "Live Print Activity",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Live progress, preparation states, and controls for prints in progress"
                setShowBadge(true)
                setSound(null, null)
                enableVibration(false)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            }
            manager.createNotificationChannel(liveChannel)

            // 2. Completion Channel
            val completeChannel = NotificationChannel(
                COMPLETION_CHANNEL_ID,
                "Print Completed Alerts",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Alerts when a 3D print finishes or needs attention"
                setShowBadge(true)
                enableVibration(true)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            }
            manager.createNotificationChannel(completeChannel)
        }
    }

    fun buildLiveNotification(
        context: Context,
        status: LivePrintStatus,
        thumbnailBitmap: Bitmap? = null
    ): Notification {
        createNotificationChannels(context)

        val notificationId = NOTIFICATION_ID_BASE + status.printerId

        // Content intent: open PrintHive MainActivity
        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("printer_id", status.printerId)
        }
        val contentPendingIntent = PendingIntent.getActivity(
            context,
            notificationId,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val isPreparing = status.state == "PREPARE" || (!status.stageName.isNullOrBlank() && status.stageName != "Printing" && status.progress <= 1)
        val isPaused = status.state == "PAUSE"

        // Build Title
        val title = when {
            isPaused -> "${status.printerName} • Paused (${status.progress}%)"
            isPreparing -> "${status.printerName} • ${status.stageName ?: "Preparing"}"
            else -> "${status.printerName} • ${status.progress}%"
        }

        // Build Details Line
        val fileName = status.subtaskName?.substringAfterLast('/')?.takeIf { it.isNotBlank() } ?: "3D Print"
        val layerInfo = if (status.layerNum != null && status.totalLayers != null && status.totalLayers > 0) {
            "Layer ${status.layerNum}/${status.totalLayers}"
        } else null

        val etaInfo = status.remainingSeconds?.takeIf { it > 0 }?.let { formatEta(it) }

        val detailsList = mutableListOf<String>()
        detailsList.add(fileName)
        if (layerInfo != null) detailsList.add(layerInfo)
        if (etaInfo != null) detailsList.add(etaInfo)

        val contentText = detailsList.joinToString(" • ")

        // Subtext / Hardware info (Filament + Temperatures)
        val filamentText = buildFilamentLabel(status.filamentType, status.filamentColorHex)
        val tempsText = buildTempsLabel(status.nozzleTemp, status.bedTemp)
        val subText = listOfNotNull(filamentText, tempsText).joinToString("  |  ")

        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(contentText)
            .setContentIntent(contentPendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)

        if (subText.isNotBlank()) {
            builder.setSubText(subText)
        }

        // Progress bar
        if (isPreparing && status.progress <= 0) {
            builder.setProgress(0, 0, true) // Indeterminate during preparation/homing
        } else {
            builder.setProgress(100, status.progress.coerceIn(0, 100), false)
        }

        // Android 16 & 17 Rich Ongoing Notification / Live Activity Extras
        val extras = Bundle().apply {
            putBoolean("android.requestPromotedOngoing", true)
            putString("android.substName", status.printerName)
            if (layerInfo != null) putString("android.shortCriticalText", "${status.progress}%")
        }
        builder.addExtras(extras)

        val swatchBitmap = createColorSwatchBitmap(status.filamentColorHex)

        // Thumbnail / Large Icon
        if (thumbnailBitmap != null) {
            builder.setLargeIcon(thumbnailBitmap)
            val bigPictureStyle = NotificationCompat.BigPictureStyle()
                .bigPicture(thumbnailBitmap)
                .bigLargeIcon(swatchBitmap) // Swatch circle in corner when expanded
                .setBigContentTitle(title)
                .setSummaryText(contentText)
            builder.setStyle(bigPictureStyle)
        } else {
            if (swatchBitmap != null) {
                builder.setLargeIcon(swatchBitmap)
            }
            val bigTextStyle = NotificationCompat.BigTextStyle()
                .setBigContentTitle(title)
                .bigText("$contentText\n$subText")
            builder.setStyle(bigTextStyle)
        }

        // Action Buttons: Pause / Resume / Cancel
        if (isPaused) {
            val resumePendingIntent = PrintNotificationActionReceiver.createActionPendingIntent(
                context, status.printerId, PrintNotificationActionReceiver.ACTION_RESUME
            )
            builder.addAction(
                android.R.drawable.ic_media_play,
                "Resume",
                resumePendingIntent
            )
        } else {
            val pausePendingIntent = PrintNotificationActionReceiver.createActionPendingIntent(
                context, status.printerId, PrintNotificationActionReceiver.ACTION_PAUSE
            )
            builder.addAction(
                android.R.drawable.ic_media_pause,
                "Pause",
                pausePendingIntent
            )
        }

        val stopPendingIntent = PrintNotificationActionReceiver.createActionPendingIntent(
            context, status.printerId, PrintNotificationActionReceiver.ACTION_STOP
        )
        builder.addAction(
            android.R.drawable.ic_menu_close_clear_cancel,
            "Cancel",
            stopPendingIntent
        )

        return builder.build()
    }

    fun showCompletionNotification(context: Context, printerId: Int, printerName: String, fileName: String?) {
        createNotificationChannels(context)

        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("printer_id", printerId)
        }
        val contentPendingIntent = PendingIntent.getActivity(
            context,
            NOTIFICATION_ID_BASE + 500 + printerId,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val cleanName = fileName?.substringAfterLast('/') ?: "3D Print"
        val notification = NotificationCompat.Builder(context, COMPLETION_CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("$printerName • Print Finished! 🎉")
            .setContentText("$cleanName completed successfully.")
            .setContentIntent(contentPendingIntent)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()

        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID_BASE + 500 + printerId, notification)
    }

    fun dismissLiveNotification(context: Context, printerId: Int) {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.cancel(NOTIFICATION_ID_BASE + printerId)
    }

    private fun formatEta(seconds: Int): String {
        val hours = seconds / 3600
        val minutes = (seconds % 3600) / 60
        return when {
            hours > 0 -> "${hours}h ${minutes}m left"
            minutes > 0 -> "${minutes}m left"
            else -> "<1m left"
        }
    }

    private fun buildFilamentLabel(type: String?, colorHex: String?): String? {
        if (type.isNullOrBlank() && colorHex.isNullOrBlank()) return null
        val dot = "●"
        val cleanType = type?.trim() ?: "Filament"
        return if (!colorHex.isNullOrBlank()) {
            val formattedHex = if (colorHex.startsWith("#")) colorHex else "#$colorHex"
            "$dot $cleanType ($formattedHex)"
        } else {
            "$dot $cleanType"
        }
    }

    private fun buildTempsLabel(nozzle: Float?, bed: Float?): String? {
        val n = nozzle?.toInt()?.takeIf { it > 0 }
        val b = bed?.toInt()?.takeIf { it > 0 }
        return when {
            n != null && b != null -> "🌡️ ${n}° / ${b}°C"
            n != null -> "🌡️ ${n}°C"
            b != null -> "🛏️ ${b}°C"
            else -> null
        }
    }

    private fun createColorSwatchBitmap(hexString: String?): Bitmap? {
        if (hexString.isNullOrBlank()) return null
        return try {
            val cleanHex = when {
                hexString.startsWith("#") -> hexString
                hexString.length in (6..8) -> "#$hexString"
                else -> return null
            }
            val color = Color.parseColor(cleanHex)
            val size = 64
            val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bitmap)
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                this.color = color
                style = Paint.Style.FILL
            }
            canvas.drawCircle(size / 2f, size / 2f, size / 2f - 2f, paint)

            // Outline
            val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                this.color = Color.DKGRAY
                style = Paint.Style.STROKE
                strokeWidth = 3f
            }
            canvas.drawCircle(size / 2f, size / 2f, size / 2f - 2f, borderPaint)
            bitmap
        } catch (e: Exception) {
            null
        }
    }
}
