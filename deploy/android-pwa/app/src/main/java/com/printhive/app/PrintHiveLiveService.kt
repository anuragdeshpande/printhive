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

    // Cache thumbnail bitmaps (up to 20 images) to avoid re-downloading every loop and prevent multi-printer collisions
    private val thumbnailCache = object : android.util.LruCache<String, Bitmap>(20) {}

    // Track active printer IDs and last known jobs
    data class CachedPrintJob(val name: String, val coverUrl: String?)
    private val lastKnownJob = mutableMapOf<Int, CachedPrintJob>()
    private val activePrinters = mutableMapOf<Int, LivePrintStatus>()
    private val notifiedCompletions = mutableMapOf<Int, String>() // printerId -> jobTag
    private var isForegroundServiceStarted = false
    private var currentForegroundId = 0

    private fun saveLastKnownJob(printerId: Int, job: CachedPrintJob) {
        try {
            val prefs = getSharedPreferences("printhive_last_known_jobs", Context.MODE_PRIVATE)
            prefs.edit()
                .putString("name_$printerId", job.name)
                .putString("cover_$printerId", job.coverUrl)
                .apply()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to save last known job for printer $printerId: ${e.message}")
        }
    }

    private fun loadLastKnownJob(printerId: Int): CachedPrintJob? {
        return try {
            val prefs = getSharedPreferences("printhive_last_known_jobs", Context.MODE_PRIVATE)
            val name = prefs.getString("name_$printerId", null) ?: return null
            val cover = prefs.getString("cover_$printerId", null)
            CachedPrintJob(name, cover)
        } catch (e: Exception) {
            null
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Log.d(TAG, "PrintHiveLiveService created")
        LiveNotificationManager.createNotificationChannels(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!isForegroundServiceStarted) {
            val initialNotification = NotificationCompat.Builder(this, LiveNotificationManager.CHANNEL_ID)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle("PrintHive Monitoring")
                .setContentText("Connecting to 3D printers...")
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setShowWhen(false)
                .setOngoing(true)
                .setLocalOnly(true)
                .apply {
                    if (Build.VERSION.SDK_INT >= 31) {
                        setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
                    }
                }
                .build()
            try {
                startForeground(LiveNotificationManager.NOTIFICATION_ID_BASE, initialNotification)
                currentForegroundId = LiveNotificationManager.NOTIFICATION_ID_BASE
                isForegroundServiceStarted = true
            } catch (e: Exception) {
                Log.w(TAG, "Could not start foreground immediately: ${e.message}")
            }
        }

        startPollingLoop()

        if (intent?.action == ACTION_POLL_NOW) {
            serviceScope.launch { pollPrintersOnce() }
        }

        return START_NOT_STICKY
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
            val queueJson = fetchQueue()

            val currentActivePrinters = mutableListOf<LivePrintStatus>()
            val currentActivePrinterIds = mutableSetOf<Int>()

            for (i in 0 until printersJson.length()) {
                val printerObj = printersJson.getJSONObject(i)
                val printerId = printerObj.optInt("id", -1)
                val printerName = printerObj.optString("name", "3D Printer")
                val printerModel = printerObj.optString("model", "")
                if (printerId <= 0) continue

                val statusObj = fetchPrinterStatus(printerId)
                if (statusObj == null) {
                    Log.w(TAG, "Printer $printerId ($printerName): fetchPrinterStatus returned null")
                    continue
                }

                val rawState = statusObj.optString("state", "IDLE")
                val state = rawState.trim().uppercase()
                val progress = statusObj.optDouble("progress", 0.0)
                val awaitingPlateClear = statusObj.optBoolean("awaiting_plate_clear", false)

                Log.d(TAG, "Printer $printerId ($printerName): state='$state', progress=$progress%, awaitingPlateClear=$awaitingPlateClear")

                // Search queue for pending jobs and recent completed jobs for this printer
                var pendingJob: JSONObject? = null
                var lastCompletedJob: JSONObject? = null

                if (queueJson != null) {
                    for (q in 0 until queueJson.length()) {
                        val item = queueJson.optJSONObject(q) ?: continue
                        val qPrinterId = item.optInt("printer_id", -1)
                        val qTargetModel = item.optString("target_model", "")
                        val qStatus = item.optString("status", "").lowercase()

                        val isForThisPrinter = (qPrinterId == printerId || (qPrinterId <= 0 && qTargetModel.isNotBlank() && qTargetModel.equals(printerModel, ignoreCase = true)))

                        if (isForThisPrinter) {
                            if (qStatus == "pending" && pendingJob == null) {
                                pendingJob = item
                            } else if (qStatus == "completed") {
                                // Track the MOST RECENT completed queue job (highest ID), NOT the oldest!
                                val qId = item.optInt("id", -1)
                                val currentMaxId = lastCompletedJob?.optInt("id", -1) ?: -1
                                if (qId > currentMaxId) {
                                    lastCompletedJob = item
                                }
                            }
                        }
                    }
                }

                val isActivelyPrinting = state in listOf(
                    "RUNNING", "PRINTING", "WORKING",
                    "PAUSE", "PAUSED",
                    "PREPARE", "PREPARING", "BUSY"
                ) || (progress > 0.0 && progress < 100.0 && state !in listOf("IDLE", "FINISH", "FAILED", "OFFLINE"))

                if (isActivelyPrinting) {
                    NotificationDismissalManager.clearForPrinter(this, printerId)
                    notifiedCompletions.remove(printerId)

                    val status = parsePrinterStatus(printerId, printerName, statusObj)
                    status.subtaskName?.takeIf { it.isNotBlank() }?.let { name ->
                        val oldJob = lastKnownJob[printerId] ?: loadLastKnownJob(printerId)
                        if (oldJob != null && (oldJob.name != name || (oldJob.coverUrl != null && oldJob.coverUrl != status.coverUrl))) {
                            Log.i(TAG, "Printer $printerId job changed from '${oldJob.name}' to '$name'. Evicting old thumbnail cache.")
                            oldJob.coverUrl?.let { thumbnailCache.remove(it) }
                            thumbnailCache.snapshot().keys.filter { it.contains("/printers/$printerId/") }.forEach {
                                thumbnailCache.remove(it)
                            }
                        }
                        val cachedJob = CachedPrintJob(name, status.coverUrl)
                        lastKnownJob[printerId] = cachedJob
                        saveLastKnownJob(printerId, cachedJob)
                    }
                    currentActivePrinters.add(status)
                    currentActivePrinterIds.add(printerId)
                } else if (awaitingPlateClear) {
                    // Resolve finished print details using active memory, backend print-log, and recent queue job
                    val cached = lastKnownJob[printerId] ?: loadLastKnownJob(printerId)
                    val printLogEntry = fetchLatestPrintLog(printerId)

                    val finishedName = sequenceOf(
                        statusObj.optString("subtask_name").takeIf { it.isNotBlank() },
                        statusObj.optString("current_print").takeIf { it.isNotBlank() },
                        cached?.name,
                        printLogEntry?.optString("print_name")?.takeIf { it.isNotBlank() },
                        lastCompletedJob?.optString("archive_name")?.takeIf { it.isNotBlank() }
                    ).firstOrNull { !it.isNullOrBlank() } ?: "3D Print"

                    val finishedArchiveId = printLogEntry?.optInt("archive_id", -1)?.takeIf { it > 0 }
                        ?: statusObj.optInt("current_archive_id", -1).takeIf { it > 0 }
                        ?: lastCompletedJob?.optInt("archive_id", -1)?.takeIf { it > 0 }
                    val finishedLogId = printLogEntry?.optInt("id", -1)?.takeIf { it > 0 }

                    val candidateCovers = mutableListOf<String>()
                    finishedLogId?.let { candidateCovers.add("https://192.168.1.250/api/v1/print-log/$it/thumbnail") }
                    finishedArchiveId?.let { candidateCovers.add("https://192.168.1.250/api/v1/archives/$it/thumbnail") }
                    val encodedFinishedName = try { java.net.URLEncoder.encode(finishedName, "UTF-8") } catch (e: Exception) { "" }
                    candidateCovers.add("https://192.168.1.250/api/v1/printers/$printerId/cover?archive=${finishedArchiveId ?: ""}&job=$encodedFinishedName")
                    cached?.coverUrl?.let { candidateCovers.add(it) }
                    statusObj.optString("cover_url").takeIf { it.isNotBlank() }?.let {
                        val full = if (it.startsWith("http")) it else "https://192.168.1.250$it"
                        candidateCovers.add(full)
                    }

                    val finishedCover = candidateCovers.firstOrNull()

                    val oldJob = lastKnownJob[printerId]
                    if (oldJob != null && oldJob.name != finishedName) {
                        Log.i(TAG, "Printer $printerId finished job changed from '${oldJob.name}' to '$finishedName'. Evicting old thumbnail cache.")
                        oldJob.coverUrl?.let { thumbnailCache.remove(it) }
                        thumbnailCache.snapshot().keys.filter { it.contains("/printers/$printerId/") }.forEach {
                            thumbnailCache.remove(it)
                        }
                    }
                    val cachedJob = CachedPrintJob(finishedName, finishedCover)
                    lastKnownJob[printerId] = cachedJob
                    saveLastKnownJob(printerId, cachedJob)

                    // Pre-fetch thumbnail with fallbacks
                    val preloadedBitmap = fetchThumbnailIfNeeded(finishedCover, *candidateCovers.drop(1).toTypedArray())

                    val finishedTag = finishedArchiveId?.toString()
                        ?: finishedLogId?.toString()
                        ?: finishedName

                    if (pendingJob != null) {
                        // SCENARIO B: Next job is in queue waiting for plate to be cleared!
                        val newJobName = sequenceOf(
                            pendingJob.optString("archive_name"),
                            pendingJob.optString("library_file_name")
                        ).firstOrNull { !it.isNullOrBlank() } ?: "Queued Print"

                        val newJobArchiveId = pendingJob.optInt("archive_id", -1).takeIf { it > 0 }
                        val newJobLibId = pendingJob.optInt("library_file_id", -1).takeIf { it > 0 }
                        val newJobCover = when {
                            newJobArchiveId != null -> "https://192.168.1.250/api/v1/archives/$newJobArchiveId/thumbnail"
                            newJobLibId != null -> "https://192.168.1.250/api/v1/library/files/$newJobLibId/thumbnail"
                            else -> null
                        }

                        val tempsObj = statusObj.optJSONObject("temperatures")
                        val nozzleTemp = tempsObj?.optDouble("nozzle", -1.0)?.takeIf { it > 0 }?.toFloat()
                        val bedTemp = tempsObj?.optDouble("bed", -1.0)?.takeIf { it > 0 }?.toFloat()

                        val waitingStatus = LivePrintStatus(
                            printerId = printerId,
                            printerName = printerName,
                            state = "WAITING",
                            subtaskName = newJobName,
                            progress = 0,
                            remainingSeconds = null,
                            layerNum = null,
                            totalLayers = null,
                            nozzleTemp = nozzleTemp,
                            bedTemp = bedTemp,
                            stageName = "Waiting for plate to be cleared",
                            filamentType = pendingJob.optString("filament_type").takeIf { it.isNotBlank() },
                            filamentColorHex = pendingJob.optString("filament_color").takeIf { it.isNotBlank() },
                            coverUrl = newJobCover,
                            isWaitingForPlateClear = true,
                            isFinishedAwaitingClear = false
                        )
                        currentActivePrinters.add(waitingStatus)
                        currentActivePrinterIds.add(printerId)

                        // Move previous finished print into a dismissable completion notification (ONLY once, and if not dismissed!)
                        val isDismissed = NotificationDismissalManager.isDismissed(this, printerId, finishedTag)
                        val alreadyNotified = (notifiedCompletions[printerId] == finishedTag)

                        if (!isDismissed && !alreadyNotified) {
                            val finishedThumbnail = fetchThumbnailIfNeeded(finishedCover)
                            LiveNotificationManager.showCompletionNotification(
                                this,
                                printerId,
                                printerName,
                                finishedName,
                                finishedThumbnail,
                                finishedTag
                            )
                            notifiedCompletions[printerId] = finishedTag
                        }
                    } else {
                        // SCENARIO C: No next print queued; show the finished print as Live Activity if NOT dismissed!
                        val isDismissed = NotificationDismissalManager.isDismissed(this, printerId, finishedTag)
                        if (isDismissed) {
                            Log.d(TAG, "Printer $printerId ($printerName) finished notification was dismissed by user ($finishedTag). Skipping.")
                        } else {
                            val tempsObj = statusObj.optJSONObject("temperatures")
                            val nozzleTemp = tempsObj?.optDouble("nozzle", -1.0)?.takeIf { it > 0 }?.toFloat()
                            val bedTemp = tempsObj?.optDouble("bed", -1.0)?.takeIf { it > 0 }?.toFloat()

                            val finishedStatus = LivePrintStatus(
                                printerId = printerId,
                                printerName = printerName,
                                state = "FINISH",
                                subtaskName = finishedName,
                                progress = 100,
                                remainingSeconds = 0,
                                layerNum = statusObj.optInt("layer_num", 0).takeIf { it > 0 },
                                totalLayers = statusObj.optInt("total_layers", 0).takeIf { it > 0 },
                                nozzleTemp = nozzleTemp,
                                bedTemp = bedTemp,
                                stageName = "Plate waiting to be cleared",
                                filamentType = null,
                                filamentColorHex = null,
                                coverUrl = finishedCover,
                                isWaitingForPlateClear = false,
                                isFinishedAwaitingClear = true
                            )
                            currentActivePrinters.add(finishedStatus)
                            currentActivePrinterIds.add(printerId)
                        }
                    }
                } else if (activePrinters.containsKey(printerId)) {
                    // Printer was active but now idle/cleared
                    val lastStatus = activePrinters[printerId]
                    if ((state == "FINISH" || state == "FINISHED")
                        && lastStatus != null
                        && !lastStatus.isFinishedAwaitingClear
                        && !lastStatus.isWaitingForPlateClear
                        && lastStatus.state != "WAITING"
                    ) {
                        val jobTag = lastStatus.subtaskName ?: "finished"
                        if (!NotificationDismissalManager.isDismissed(this, printerId, jobTag) && notifiedCompletions[printerId] != jobTag) {
                            val thumb = fetchThumbnailIfNeeded(lastStatus.coverUrl)
                            LiveNotificationManager.showCompletionNotification(
                                this, printerId, printerName, lastStatus.subtaskName, thumb, jobTag
                            )
                            notifiedCompletions[printerId] = jobTag
                        }
                    }
                    LiveNotificationManager.dismissLiveNotification(this, printerId)
                    activePrinters.remove(printerId)
                }
            }

            val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager

            // Dismiss any live notifications for printers that are no longer active/waiting/finished-awaiting
            val staleIds = activePrinters.keys.filter { it !in currentActivePrinterIds }
            for (staleId in staleIds) {
                val notifId = LiveNotificationManager.NOTIFICATION_ID_BASE + staleId
                if (notifId != currentForegroundId) {
                    LiveNotificationManager.dismissLiveNotification(this, staleId)
                }
                activePrinters.remove(staleId)
            }

            // Update notifications for active prints in place
            if (currentActivePrinters.isNotEmpty()) {
                val hasMultiplePrinters = currentActivePrinters.size > 1

                if (hasMultiplePrinters) {
                    val summaryNotification = LiveNotificationManager.buildGroupSummaryNotification(
                        this,
                        currentActivePrinters.size,
                        currentActivePrinters.map { it.printerName }
                    )
                    if (currentForegroundId != LiveNotificationManager.NOTIFICATION_ID_BASE) {
                        if (currentForegroundId != 0) {
                            stopForeground(STOP_FOREGROUND_REMOVE)
                            notificationManager.cancel(currentForegroundId)
                        }
                        startForeground(LiveNotificationManager.NOTIFICATION_ID_BASE, summaryNotification)
                        currentForegroundId = LiveNotificationManager.NOTIFICATION_ID_BASE
                    } else {
                        notificationManager.notify(LiveNotificationManager.NOTIFICATION_ID_BASE, summaryNotification)
                    }
                }

                for ((index, status) in currentActivePrinters.withIndex()) {
                    val notificationId = LiveNotificationManager.NOTIFICATION_ID_BASE + status.printerId
                    val prevStatus = activePrinters[status.printerId]
                    val isFirstPost = (prevStatus == null)

                    val isVisuallySame = !isFirstPost &&
                        prevStatus.state == status.state &&
                        prevStatus.progress == status.progress &&
                        prevStatus.remainingSeconds == status.remainingSeconds &&
                        prevStatus.layerNum == status.layerNum &&
                        prevStatus.stageName == status.stageName &&
                        prevStatus.subtaskName == status.subtaskName &&
                        prevStatus.nozzleTemp?.toInt() == status.nozzleTemp?.toInt() &&
                        prevStatus.bedTemp?.toInt() == status.bedTemp?.toInt() &&
                        prevStatus.isWaitingForPlateClear == status.isWaitingForPlateClear &&
                        prevStatus.isFinishedAwaitingClear == status.isFinishedAwaitingClear

                    if (isVisuallySame && (hasMultiplePrinters || currentForegroundId == notificationId)) {
                        activePrinters[status.printerId] = status
                        continue
                    }

                    val candidateCovers = mutableListOf<String>()
                    if (!status.coverUrl.isNullOrBlank()) candidateCovers.add(status.coverUrl)
                    if (status.currentArchiveId != null) {
                        candidateCovers.add("https://192.168.1.250/api/v1/archives/${status.currentArchiveId}/thumbnail")
                    }
                    candidateCovers.add("https://192.168.1.250/api/v1/printers/${status.printerId}/cover")
                    val thumbnail = fetchThumbnailIfNeeded(candidateCovers.firstOrNull(), *candidateCovers.drop(1).toTypedArray())
                    val promoteOngoing = (index == 0) // First printer gets promoted ongoing status bar chip
                    val notification = LiveNotificationManager.buildLiveNotification(this, status, thumbnail, promoteOngoing)

                    Log.d(TAG, "Posting live notification for printer ${status.printerId} (${status.printerName}) with ID $notificationId (foreground=${!hasMultiplePrinters && index == 0})")

                    if (!hasMultiplePrinters) {
                        if (currentForegroundId != notificationId) {
                            if (currentForegroundId != 0) {
                                stopForeground(STOP_FOREGROUND_REMOVE)
                                notificationManager.cancel(currentForegroundId)
                            }
                            startForeground(notificationId, notification)
                            currentForegroundId = notificationId
                        } else {
                            notificationManager.notify(notificationId, notification)
                        }
                    } else {
                        notificationManager.notify(notificationId, notification)
                    }
                    activePrinters[status.printerId] = status
                }
            } else {
                // Ensure idle notification is maintained for the foreground service
                if (currentForegroundId != LiveNotificationManager.NOTIFICATION_ID_BASE) {
                    val idleNotification = NotificationCompat.Builder(this, LiveNotificationManager.CHANNEL_ID)
                        .setSmallIcon(R.mipmap.ic_launcher)
                        .setContentTitle("PrintHive Monitoring")
                        .setContentText("Connected • Idle")
                        .setPriority(NotificationCompat.PRIORITY_LOW)
                        .setShowWhen(false)
                        .setOngoing(true)
                        .setLocalOnly(true)
                        .apply {
                            if (Build.VERSION.SDK_INT >= 31) {
                                setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
                            }
                        }
                        .build()
                    if (currentForegroundId != 0) {
                        stopForeground(STOP_FOREGROUND_REMOVE)
                        notificationManager.cancel(currentForegroundId)
                    }
                    startForeground(LiveNotificationManager.NOTIFICATION_ID_BASE, idleNotification)
                    currentForegroundId = LiveNotificationManager.NOTIFICATION_ID_BASE
                }
            }

            currentActivePrinters.isNotEmpty()
        } catch (e: Exception) {
            Log.e(TAG, "Error polling printers", e)
            false
        }
    }

    private fun fetchQueue(): JSONArray? {
        return try {
            val requestBuilder = Request.Builder()
                .url("https://192.168.1.250/api/v1/queue/")
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
                response.body?.string()?.let { JSONArray(it) }
            } else null
        } catch (e: Exception) {
            Log.w(TAG, "Failed to fetch print queue: ${e.message}")
            null
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

    private fun fetchLatestPrintLog(printerId: Int): JSONObject? {
        return try {
            val requestBuilder = Request.Builder()
                .url("https://192.168.1.250/api/v1/print-log/?printer_id=$printerId&limit=1")
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
                val body = response.body?.string() ?: return null
                val json = JSONObject(body)
                val items = json.optJSONArray("items")
                if (items != null && items.length() > 0) {
                    items.getJSONObject(0)
                } else null
            } else {
                Log.w(TAG, "Failed to fetch print-log for printer $printerId: HTTP ${response.code}")
                null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error fetching print log for printer $printerId: ${e.message}")
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

        val currentArchiveId = json.optInt("current_archive_id", -1).takeIf { it > 0 }
        val stageName = json.optString("stg_cur_name", "").takeIf { it.isNotBlank() }
        val coverPath = json.optString("cover_url", "").takeIf { it.isNotBlank() }
        val fullCoverUrl = coverPath?.let {
            val base = if (it.startsWith("http")) it else "https://192.168.1.250$it"
            val queryParts = mutableListOf<String>()
            if (currentArchiveId != null) queryParts.add("archive=$currentArchiveId")
            if (!subtaskName.isNullOrBlank()) {
                val encoded = java.net.URLEncoder.encode(subtaskName, "UTF-8")
                queryParts.add("job=$encoded")
            }
            if (queryParts.isNotEmpty()) {
                val sep = if (base.contains("?")) "&" else "?"
                "$base$sep${queryParts.joinToString("&")}"
            } else base
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
            coverUrl = fullCoverUrl,
            currentArchiveId = currentArchiveId
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

    private fun fetchThumbnailIfNeeded(url: String?, vararg fallbackUrls: String?): Bitmap? {
        val candidates = mutableListOf<String>()
        if (!url.isNullOrBlank()) candidates.add(url)
        for (fb in fallbackUrls) {
            if (!fb.isNullOrBlank() && fb !in candidates) {
                candidates.add(fb)
            }
        }
        if (candidates.isEmpty()) return null

        for (u in candidates) {
            thumbnailCache.get(u)?.let { return it }
        }

        for (candUrl in candidates) {
            val bitmap = downloadThumbnail(candUrl)
            if (bitmap != null) {
                if (!url.isNullOrBlank()) thumbnailCache.put(url, bitmap)
                thumbnailCache.put(candUrl, bitmap)
                return bitmap
            }
        }
        return null
    }

    private fun downloadThumbnail(url: String): Bitmap? {
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
                    if (bitmap != null) {
                        Log.d(TAG, "Successfully downloaded thumbnail (${bitmap.width}x${bitmap.height}) from $finalUrl")
                    }
                    bitmap
                }
            } else {
                if (response.code == 401) {
                    cachedStreamToken = null
                }
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
