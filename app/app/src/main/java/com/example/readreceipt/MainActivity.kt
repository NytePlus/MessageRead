package com.example.readreceipt

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class CreateResp(val id: String, val openUrl: String, val statusUrl: String)
data class StatusResp(val read: Boolean, val readAt: Long?, val createdAt: Long)
data class SentRecord(val uuid: String, val createdAt: Long)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                AppScreen()
            }
        }
    }
}

@Composable
fun AppScreen() {
    val context = LocalContext.current
    var toName by remember { mutableStateOf("") }
    var body by remember { mutableStateOf("") }
    var pending by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var result by remember { mutableStateOf<CreateResp?>(null) }
    var status by remember { mutableStateOf<StatusResp?>(null) }
    var records by remember { mutableStateOf(loadSentRecords(context)) }

    LaunchedEffect(result?.id) {
        val current = result ?: return@LaunchedEffect
        while (true) {
            runCatching { fetchStatus(current.id) }.onSuccess { status = it }
            delay(2500)
        }
    }

    Surface(modifier = Modifier.fillMaxSize()) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.radialGradient(
                        listOf(
                            androidx.compose.ui.graphics.Color(0xFF1E3A5F),
                            androidx.compose.ui.graphics.Color(0xFF0F172A)
                        )
                    )
                )
                .padding(24.dp),
            contentAlignment = Alignment.Center
        ) {
            Card(
                modifier = Modifier
                    .widthIn(max = 460.dp)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
                colors = CardDefaults.cardColors(
                    containerColor = androidx.compose.ui.graphics.Color(0xCC0F172A)
                ),
                shape = RoundedCornerShape(20.dp),
                elevation = CardDefaults.cardElevation(defaultElevation = 18.dp)
            ) {
                Column(modifier = Modifier.padding(26.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text("消息已读统计", color = androidx.compose.ui.graphics.Color.White, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        "填写内容后生成链接与二维码；首次打开者记为本人，其他浏览器打开才记为已读。",
                        color = androidx.compose.ui.graphics.Color(0xFF94A3B8),
                        fontSize = 14.sp,
                        lineHeight = 21.sp
                    )

                    OutlinedTextField(
                        value = toName,
                        onValueChange = { toName = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("TA 的名字") },
                        placeholder = { Text("例如：小明") },
                        singleLine = true
                    )
                    OutlinedTextField(
                        value = body,
                        onValueChange = { body = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("你想对 TA 说的话") },
                        placeholder = { Text("写在这里...") },
                        minLines = 5
                    )

                    error?.let {
                        Text(it, color = androidx.compose.ui.graphics.Color(0xFFFCA5A5), fontSize = 14.sp)
                    }

                    Button(
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !pending,
                        colors = ButtonDefaults.buttonColors(containerColor = androidx.compose.ui.graphics.Color(0xFF0EA5E9)),
                        onClick = {
                            pending = true
                            error = null
                            result = null
                            status = null
                        }
                    ) {
                        Text(if (pending) "提交中..." else "提交消息", fontWeight = FontWeight.SemiBold)
                    }

                    LaunchedEffect(pending) {
                        if (!pending) return@LaunchedEffect
                        val name = toName.trim()
                        val text = body.trim()
                        if (name.isEmpty() || text.isEmpty()) {
                            error = "需要提供对方名字和要说的内容。"
                            pending = false
                            return@LaunchedEffect
                        }
                        runCatching { createMessage(name, text) }
                            .onSuccess {
                                result = it
                                records = saveSentRecord(context, SentRecord(it.id, System.currentTimeMillis()))
                            }
                            .onFailure {
                                error = "无法连接后端，请确认服务已启动。"
                            }
                        pending = false
                    }

                    result?.let { created ->
                        ResultPanel(created, status)
                    }

                    if (records.isNotEmpty()) {
                        SentRecords(records)
                    }
                }
            }
        }
    }
}

@Composable
fun ResultPanel(result: CreateResp, status: StatusResp?) {
    val context = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Spacer(Modifier.height(6.dp))
        Text("分享链接", color = androidx.compose.ui.graphics.Color.White, fontWeight = FontWeight.SemiBold)
        Text(result.openUrl, color = androidx.compose.ui.graphics.Color(0xFF7DD3FC), fontSize = 13.sp)
        Box(
            modifier = Modifier
                .align(Alignment.CenterHorizontally)
                .background(androidx.compose.ui.graphics.Color.White, RoundedCornerShape(14.dp))
                .padding(16.dp)
        ) {
            Image(bitmap = qrBitmap(result.openUrl).asImageBitmap(), contentDescription = "二维码", modifier = Modifier.size(200.dp))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("已读状态", color = androidx.compose.ui.graphics.Color(0xFF94A3B8), fontSize = 14.sp)
            val readText = if (status?.read == true) {
                "已读" + (status.readAt?.let { " · ${formatTime(it)}" } ?: "")
            } else {
                "未读"
            }
            Text(readText, color = if (status?.read == true) androidx.compose.ui.graphics.Color(0xFF86EFAC) else androidx.compose.ui.graphics.Color(0xFFFCD34D))
        }
        Button(
            modifier = Modifier.fillMaxWidth(),
            onClick = {
                val intent = Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_TEXT, result.openUrl)
                }
                context.startActivity(Intent.createChooser(intent, "分享链接"))
            }
        ) {
            Text("分享链接")
        }
    }
}

@Composable
fun SentRecords(records: List<SentRecord>) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("本机发送记录", color = androidx.compose.ui.graphics.Color.White, fontWeight = FontWeight.SemiBold)
        records.take(5).forEach {
            Text("${it.uuid} · ${formatTime(it.createdAt)}", color = androidx.compose.ui.graphics.Color(0xFFCBD5E1), fontSize = 13.sp)
        }
        if (records.size > 5) {
            Text("还有 ${records.size - 5} 条记录", color = androidx.compose.ui.graphics.Color(0xFF94A3B8), fontSize = 12.sp, textAlign = TextAlign.Start)
        }
    }
}

suspend fun createMessage(toName: String, body: String): CreateResp = withContext(Dispatchers.IO) {
    val payload = JSONObject().put("toName", toName).put("body", body).toString().toByteArray(StandardCharsets.UTF_8)
    val conn = (URL("${BuildConfig.API_BASE_URL}/api/messages").openConnection() as HttpURLConnection).apply {
        requestMethod = "POST"
        setRequestProperty("Content-Type", "application/json")
        doOutput = true
        outputStream.use { it.write(payload) }
    }
    val text = conn.inputStream.bufferedReader().use { it.readText() }
    val obj = JSONObject(text)
    CreateResp(obj.getString("id"), obj.getString("openUrl"), obj.getString("statusUrl"))
}

suspend fun fetchStatus(id: String): StatusResp = withContext(Dispatchers.IO) {
    val conn = URL("${BuildConfig.API_BASE_URL}/api/messages/$id/status").openConnection() as HttpURLConnection
    val text = conn.inputStream.bufferedReader().use { it.readText() }
    val obj = JSONObject(text)
    StatusResp(
        read = obj.getBoolean("read"),
        readAt = if (obj.isNull("readAt")) null else obj.getLong("readAt"),
        createdAt = obj.getLong("createdAt")
    )
}

fun qrBitmap(value: String): Bitmap {
    val matrix = QRCodeWriter().encode(value, BarcodeFormat.QR_CODE, 200, 200)
    val bitmap = Bitmap.createBitmap(200, 200, Bitmap.Config.ARGB_8888)
    for (x in 0 until 200) {
        for (y in 0 until 200) {
            bitmap.setPixel(x, y, if (matrix[x, y]) Color.BLACK else Color.WHITE)
        }
    }
    return bitmap
}

fun loadSentRecords(context: Context): List<SentRecord> {
    val prefs = context.getSharedPreferences("sent_records", Context.MODE_PRIVATE)
    val raw = prefs.getString("items", "[]") ?: "[]"
    val arr = JSONArray(raw)
    return (0 until arr.length()).mapNotNull { index ->
        val obj = arr.optJSONObject(index) ?: return@mapNotNull null
        SentRecord(obj.optString("uuid"), obj.optLong("createdAt"))
    }.filter { it.uuid.isNotBlank() }
}

fun saveSentRecord(context: Context, record: SentRecord): List<SentRecord> {
    val next = (listOf(record) + loadSentRecords(context).filter { it.uuid != record.uuid }).take(50)
    val arr = JSONArray()
    next.forEach {
        arr.put(JSONObject().put("uuid", it.uuid).put("createdAt", it.createdAt))
    }
    context.getSharedPreferences("sent_records", Context.MODE_PRIVATE)
        .edit()
        .putString("items", arr.toString())
        .apply()
    return next
}

fun formatTime(ms: Long): String {
    return SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date(ms))
}
