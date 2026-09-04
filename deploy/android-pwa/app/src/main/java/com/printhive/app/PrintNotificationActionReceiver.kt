package com.printhive.app

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

class PrintNotificationActionReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_PAUSE = "com.printhive.app.ACTION_PAUSE"
        const val ACTION_RESUME = "com.printhive.app.ACTION_RESUME"
        const val ACTION_STOP = "com.printhive.app.ACTION_STOP"
        const val EXTRA_PRINTER_ID = "extra_printer_id"

        private const val TAG = "PrintHive/ActionReceiver"

        fun createActionPendingIntent(context: Context, printerId: Int, action: String): PendingIntent {
            val intent = Intent(context, PrintNotificationActionReceiver::class.java).apply {
                this.action = action
                putExtra(EXTRA_PRINTER_ID, printerId)
            }
            val requestCode = (printerId * 10) + when (action) {
                ACTION_PAUSE -> 1
                ACTION_RESUME -> 2
                ACTION_STOP -> 3
                else -> 0
            }
            return PendingIntent.getBroadcast(
                context,
                requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        }

        fun getUnsafeOkHttpClient(): OkHttpClient {
            val trustAllCerts = arrayOf<TrustManager>(object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
                override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
                override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
            })

            val sslContext = SSLContext.getInstance("SSL")
            sslContext.init(null, trustAllCerts, SecureRandom())

            return OkHttpClient.Builder()
                .sslSocketFactory(sslContext.socketFactory, trustAllCerts[0] as X509TrustManager)
                .hostnameVerifier { _, _ -> true }
                .connectTimeout(5, TimeUnit.SECONDS)
                .readTimeout(10, TimeUnit.SECONDS)
                .build()
        }
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val printerId = intent.getIntExtra(EXTRA_PRINTER_ID, -1)
        if (printerId == -1) return

        val endpointSuffix = when (action) {
            ACTION_PAUSE -> "pause"
            ACTION_RESUME -> "resume"
            ACTION_STOP -> "stop"
            else -> return
        }

        val actionName = when (action) {
            ACTION_PAUSE -> "Pausing print..."
            ACTION_RESUME -> "Resuming print..."
            ACTION_STOP -> "Cancelling print..."
            else -> ""
        }

        Toast.makeText(context, actionName, Toast.LENGTH_SHORT).show()

        val pendingResult = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val client = getUnsafeOkHttpClient()
                val url = "https://192.168.1.250/api/v1/printers/$printerId/print/$endpointSuffix"
                val requestBuilder = Request.Builder()
                    .url(url)
                    .post("{}".toRequestBody())

                AuthStore.getToken(context)?.let {
                    requestBuilder.addHeader("Authorization", "Bearer $it")
                }
                try {
                    android.webkit.CookieManager.getInstance().getCookie("https://192.168.1.250/")?.let {
                        requestBuilder.addHeader("Cookie", it)
                    }
                } catch (e: Exception) {}

                val request = requestBuilder.build()
                val response = client.newCall(request).execute()
                Log.d(TAG, "Sent $endpointSuffix for printer $printerId: code=${response.code}")

                // Signal service to refresh status immediately
                PrintHiveLiveService.triggerImmediatePoll(context)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to send $endpointSuffix command", e)
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(context, "Action failed: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            } finally {
                pendingResult.finish()
            }
        }
    }
}
