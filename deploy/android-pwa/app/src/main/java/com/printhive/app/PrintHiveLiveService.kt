package com.printhive.app

import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

class PrintHiveLiveService : Service() {

    companion object {
        private const val TAG = "PrintHive/LiveService"
        const val ACTION_START = "com.printhive.app.service.START"
        const val ACTION_POLL_NOW = "com.printhive.app.service.POLL_NOW"

        fun startService(context: Context) {
            val intent = Intent(context, PrintHiveLiveService::class.java).apply {
                action = ACTION_START
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun triggerImmediatePoll(context: Context) {
            val intent = Intent(context, PrintHiveLiveService::class.java).apply {
                action = ACTION_POLL_NOW
            }
            context.startService(intent)
        }
    }

    private val serviceScope = CoroutineScope(Dispatchers.IO + Job())
    private var pollJob: Job? = null
    private val httpClient by lazy { PrintNotificationActionReceiver.getUnsafeOkHttpClient() }

    // Cache thumbnail bitmap to avoid re-downloading every 3 seconds
    private var cachedThumbnailUrl: String? = null
    private var cachedThumbnailBitmap: Bitmap? = null

    // Track active printer IDs
    private val activePrinters = mutableMapOf<Int, LivePrintStatus>()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Log.d(TAG, "PrintHiveLiveService created")
        LiveNotificationManager.createNotificationChannels(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Initial placeholder foreground notification to satisfy Android ForegroundService requirement
        val initialNotification = NotificationCompat.Builder(this, LiveNotificationManager.CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("PrintHive Monitoring")
            .setContentText("Connecting to 3D printers...")
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        startForeground(LiveNotificationManager.NOTIFICATION_ID_BASE, initialNotification)

        startPollingLoop()

        if (intent?.action == ACTION_POLL_NOW) {
            serviceScope.launch { pollPrintersOnce() }
        }

        return START_STICKY
    }

    private fun startPollingLoop() {
        if (pollJob?.isActive == true) return
        pollJob = serviceScope.launch {
            Log.d(TAG, "Polling loop started")
            while (isActive) {
                val hasActiveJobs = pollPrintersOnce()
                // If there are active prints, poll frequently (every 3.5s). If none, poll every 15s.
                delay(if (hasActiveJobs) 3500L else 15000L)
            }
        }
    }

    private suspend fun pollPrintersOnce(): Boolean {
        return try {
            val requestBuilder = Request.Builder()
                .url("https://192.168.1.250/api/v1/printers/")
                .get()

            AuthStore.getToken(this)?.let {
                requestBuilder.addHeader("Authorization", "Bearer $it")
            }
            try {
                android.webkit.CookieManager.getInstance().getCookie("https://192.168.1.250/")?.let {
                    requestBuilder.addHeader("Cookie", it)
                }
            } catch (e: Exception) {
                Log.w(TAG, "CookieManager exception: ${e.message}")
            }

            val request = requestBuilder.build()
            val response = httpClient.newCall(request).execute()
            if (!response.isSuccessful) {
                Log.w(TAG, "Failed to fetch printers: HTTP ${response.code}")
                return false
            }

            val body = response.body?.string() ?: return false
            val printersJson = JSONArray(body)
            Log.d(TAG, "Printers fetched successfully: count=${printersJson.length()}")

            val currentActivePrinters = mutableListOf<LivePrintStatus>()

            for (i in 0 until printersJson.length()) {
                val printerObj = printersJson.getJSONObject(i)
                val printerId = printerObj.optInt("id", -1)
                val printerName = printerObj.optString("name", "3D Printer")
                if (printerId <= 0) continue

                val statusObj = fetchPrinterStatus(printerId) ?: continue
                val state = statusObj.optString("state", "IDLE")
                Log.d(TAG, "Printer $printerId ($printerName): state=$state, progress=${statusObj.optDouble("progress", 0.0)}")

                if (state in listOf("RUNNING", "PAUSE", "PREPARE")) {
                    val status = parsePrinterStatus(printerId, printerName, statusObj)
                    currentActivePrinters.add(status)
                } else if (activePrinters.containsKey(printerId)) {
                    // Printer just finished or stopped
                    val lastStatus = activePrinters[printerId]
                    if (state == "FINISH" && lastStatus != null) {
                        LiveNotificationManager.showCompletionNotification(
                            this, printerId, printerName, lastStatus.subtaskName
                        )
                    }
                    LiveNotificationManager.dismissLiveNotification(this, printerId)
                    activePrinters.remove(printerId)
                }
            }

            // Update notifications for active prints
            if (currentActivePrinters.isNotEmpty()) {
                for ((index, status) in currentActivePrinters.withIndex()) {
                    val thumbnail = fetchThumbnailIfNeeded(status.coverUrl)
                    val notification = LiveNotificationManager.buildLiveNotification(this, status, thumbnail)
                    val notificationId = LiveNotificationManager.NOTIFICATION_ID_BASE + status.printerId

                    if (index == 0) {
                        // Promote main active print as ForegroundService notification
                        startForeground(notificationId, notification)
                    } else {
                        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
                        manager.notify(notificationId, notification)
                    }
                    activePrinters[status.printerId] = status
                }
                // Cancel idle placeholder notification so only active print card is shown
                val manager = getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
                manager.cancel(LiveNotificationManager.NOTIFICATION_ID_BASE)
            } else {
                // Ensure idle notification is maintained for the foreground service
                val idleNotification = NotificationCompat.Builder(this, LiveNotificationManager.CHANNEL_ID)
                    .setSmallIcon(R.mipmap.ic_launcher)
                    .setContentTitle("PrintHive Monitoring")
                    .setContentText("Connected • Idle")
                    .setPriority(NotificationCompat.PRIORITY_LOW)
                    .setOngoing(true)
                    .build()
                startForeground(LiveNotificationManager.NOTIFICATION_ID_BASE, idleNotification)
            }

            currentActivePrinters.isNotEmpty()
        } catch (e: Exception) {
            Log.e(TAG, "Error polling printers", e)
            false
        }
    }

    private fun fetchPrinterStatus(printerId: Int): JSONObject? {
        return try {
            val requestBuilder = Request.Builder()
                .url("https://192.168.1.250/api/v1/printers/$printerId/status")
                .get()

            AuthStore.getToken(this)?.let {
                requestBuilder.addHeader("Authorization", "Bearer $it")
            }
            try {
                android.webkit.CookieManager.getInstance().getCookie("https://192.168.1.250/")?.let {
                    requestBuilder.addHeader("Cookie", it)
                }
            } catch (e: Exception) {}

            val response = httpClient.newCall(requestBuilder.build()).execute()
            if (response.isSuccessful) {
                response.body?.string()?.let { JSONObject(it) }
            } else {
                Log.w(TAG, "Failed to fetch printer $printerId status: HTTP ${response.code}")
                null
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error fetching status for printer $printerId", e)
            null
        }
    }

    private fun parsePrinterStatus(id: Int, name: String, json: JSONObject): LivePrintStatus {
        val state = json.optString("state", "RUNNING")
        val subtaskName = sequenceOf(
            json.optString("subtask_name"),
            json.optString("current_print"),
            json.optString("gcode_file")
        ).firstOrNull { !it.isNullOrBlank() }

        val progress = json.optDouble("progress", 0.0).toInt()
        val remainingMin = json.optInt("remaining_time", -1).takeIf { it >= 0 }
        val remainingSeconds = remainingMin?.times(60)

        val layerNum = json.optInt("layer_num", 0).takeIf { it > 0 }
        val totalLayers = json.optInt("total_layers", 0).takeIf { it > 0 }

        var nozzleTemp: Float? = null
        var bedTemp: Float? = null
        val tempsObj = json.optJSONObject("temperatures")
        if (tempsObj != null) {
            val n = sequenceOf(
                tempsObj.optDouble("nozzle", -1.0),
                tempsObj.optDouble("nozzle_0", -1.0),
                tempsObj.optDouble("nozzle_temp", -1.0)
            ).firstOrNull { it > 0 }?.toFloat()
            val b = sequenceOf(
                tempsObj.optDouble("bed", -1.0),
                tempsObj.optDouble("bed_temp", -1.0)
            ).firstOrNull { it > 0 }?.toFloat()
            nozzleTemp = n
            bedTemp = b
        }

        val stageName = json.optString("stg_cur_name", "").takeIf { it.isNotBlank() }
        val coverPath = json.optString("cover_url", "").takeIf { it.isNotBlank() }
        val fullCoverUrl = coverPath?.let {
            if (it.startsWith("http")) it else "https://192.168.1.250$it"
        }

        // Filament parsing
        var filamentType: String? = null
        var filamentColorHex: String? = null

        val trayNow = json.optInt("tray_now", -1)
        val amsList = json.optJSONArray("ams")
        if (amsList != null && trayNow >= 0) {
            for (i in 0 until amsList.length()) {
                val amsObj = amsList.optJSONObject(i) ?: continue
                val trays = amsObj.optJSONArray("tray") ?: continue
                for (j in 0 until trays.length()) {
                    val tray = trays.optJSONObject(j) ?: continue
                    val trayId = tray.optInt("id", -1)
                    if (trayId == trayNow || trayId == (trayNow % 4)) {
                        filamentType = sequenceOf(
                            tray.optString("tray_type"),
                            tray.optString("tray_sub_brands"),
                            tray.optString("tray_id_name")
                        ).firstOrNull { !it.isNullOrBlank() }

                        filamentColorHex = tray.optString("tray_color", "").takeIf { it.isNotBlank() }
                        break
                    }
                }
                if (filamentType != null) break
            }
        }

        return LivePrintStatus(
            printerId = id,
            printerName = name,
            state = state,
            subtaskName = subtaskName,
            progress = progress,
            remainingSeconds = remainingSeconds,
            layerNum = layerNum,
            totalLayers = totalLayers,
            nozzleTemp = nozzleTemp,
            bedTemp = bedTemp,
            stageName = stageName,
            filamentType = filamentType,
            filamentColorHex = filamentColorHex,
            coverUrl = fullCoverUrl
        )
    }

    private var cachedStreamToken: String? = null
    private var streamTokenExpiresAt: Long = 0L

    private fun getStreamToken(): String? {
        val now = System.currentTimeMillis()
        if (!cachedStreamToken.isNullOrBlank() && now < streamTokenExpiresAt) {
            return cachedStreamToken
        }
        return try {
            val requestBuilder = Request.Builder()
                .url("https://192.168.1.250/api/v1/printers/camera/stream-token")
                .post("{}".toRequestBody())

            AuthStore.getToken(this)?.let {
                requestBuilder.addHeader("Authorization", "Bearer $it")
            }

            val response = httpClient.newCall(requestBuilder.build()).execute()
            if (response.isSuccessful) {
                val json = JSONObject(response.body?.string() ?: "{}")
                val token = json.optString("token")
                if (token.isNotBlank()) {
                    cachedStreamToken = token
                    streamTokenExpiresAt = now + (15 * 60 * 1000L) // 15 mins
                    token
                } else null
            } else null
        } catch (e: Exception) {
            Log.w(TAG, "Failed to obtain stream token: ${e.message}")
            null
        }
    }

    private fun fetchThumbnailIfNeeded(url: String?): Bitmap? {
        if (url.isNullOrBlank()) return null
        if (url == cachedThumbnailUrl && cachedThumbnailBitmap != null) {
            return cachedThumbnailBitmap
        }

        return try {
            val streamToken = getStreamToken()
            val finalUrl = if (!streamToken.isNullOrBlank() && !url.contains("token=")) {
                if (url.contains("?")) "$url&token=$streamToken" else "$url?token=$streamToken"
            } else url

            val requestBuilder = Request.Builder().url(finalUrl).get()
            AuthStore.getToken(this)?.let {
                requestBuilder.addHeader("Authorization", "Bearer $it")
            }
            try {
                android.webkit.CookieManager.getInstance().getCookie("https://192.168.1.250/")?.let {
                    requestBuilder.addHeader("Cookie", it)
                }
            } catch (e: Exception) {}
            val request = requestBuilder.build()
            val response = httpClient.newCall(request).execute()
            if (response.isSuccessful) {
                response.body?.byteStream()?.use { stream ->
                    val bitmap = BitmapFactory.decodeStream(stream)
                    cachedThumbnailUrl = url
                    cachedThumbnailBitmap = bitmap
                    Log.d(TAG, "Successfully downloaded thumbnail (${bitmap.width}x${bitmap.height}) from $finalUrl")
                    bitmap
                }
            } else {
                Log.w(TAG, "Thumbnail response code ${response.code} for $finalUrl")
                null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to download print thumbnail from $url: ${e.message}")
            null
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        pollJob?.cancel()
        Log.d(TAG, "PrintHiveLiveService destroyed")
    }
}
