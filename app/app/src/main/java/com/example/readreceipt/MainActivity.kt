package com.example.readreceipt

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

data class CreateResp(val id: String, val openUrl: String, val statusUrl: String)
data class StatusResp(val read: Boolean, val readAt: Long?, val createdAt: Long)
data class Contact(val id: String, val name: String, val createdAt: Long)
data class ChatMessage(
    val localId: String,
    val contactId: String,
    val body: String,
    val createdAt: Long,
    val remoteId: String? = null,
    val openUrl: String? = null,
    val statusUrl: String? = null,
    val read: Boolean = false,
    val readAt: Long? = null,
    val sending: Boolean = false,
    val error: String? = null
)

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
    val scope = rememberCoroutineScope()
    var contacts by remember { mutableStateOf(loadContacts(context)) }
    var messages by remember { mutableStateOf(loadMessages(context)) }
    var selectedContactId by remember { mutableStateOf(contacts.firstOrNull()?.id) }
    var newContactName by remember { mutableStateOf("") }
    var messageText by remember { mutableStateOf("") }
    var qrMessage by remember { mutableStateOf<ChatMessage?>(null) }
    val selectedContact = contacts.firstOrNull { it.id == selectedContactId }
    val selectedMessages = messages
        .filter { it.contactId == selectedContactId }
        .sortedBy { it.createdAt }

    LaunchedEffect(messages.mapNotNull { it.remoteId }.joinToString()) {
        while (true) {
            val remoteMessages = messages.filter { it.remoteId != null }
            if (remoteMessages.isNotEmpty()) {
                var next = messages
                remoteMessages.forEach { message ->
                    val remoteId = message.remoteId ?: return@forEach
                    runCatching { fetchStatus(remoteId) }.onSuccess { status ->
                        next = next.map {
                            if (it.localId == message.localId) {
                                it.copy(read = status.read, readAt = status.readAt)
                            } else {
                                it
                            }
                        }
                    }
                }
                if (next != messages) {
                    messages = next
                    saveMessages(context, next)
                }
            }
            delay(3000)
        }
    }

    MaterialTheme(colorScheme = MaterialTheme.colorScheme.copy(background = androidx.compose.ui.graphics.Color(0xFFEDEDED))) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = androidx.compose.ui.graphics.Color(0xFFEDEDED)
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                Header(
                    title = selectedContact?.name ?: "微信",
                    showBack = selectedContact != null,
                    onBack = { selectedContactId = null }
                )
                if (selectedContact == null) {
                    ContactListScreen(
                        contacts = contacts,
                        messages = messages,
                        newContactName = newContactName,
                        onNewContactNameChange = { newContactName = it },
                        onCreateContact = {
                            val name = newContactName.trim()
                            if (name.isNotEmpty()) {
                                val contact = Contact(UUID.randomUUID().toString(), name, System.currentTimeMillis())
                                contacts = listOf(contact) + contacts
                                saveContacts(context, contacts)
                                selectedContactId = contact.id
                                newContactName = ""
                            }
                        },
                        onSelectContact = { selectedContactId = it.id }
                    )
                } else {
                    ChatScreen(
                        contact = selectedContact,
                        messages = selectedMessages,
                        messageText = messageText,
                        onMessageTextChange = { messageText = it },
                        onShareMessage = { qrMessage = it },
                        onClick = {
                            val text = messageText.trim()
                            if (text.isEmpty()) return@ChatScreen
                            val localMessage = ChatMessage(
                                localId = UUID.randomUUID().toString(),
                                contactId = selectedContact.id,
                                body = text,
                                createdAt = System.currentTimeMillis(),
                                sending = true
                            )
                            messages = messages + localMessage
                            saveMessages(context, messages)
                            messageText = ""
                            scope.launch {
                                runCatching { createMessage(selectedContact.name, text) }
                                    .onSuccess { created ->
                                        val next = messages.map {
                                            if (it.localId == localMessage.localId) {
                                                it.copy(
                                                    remoteId = created.id,
                                                    openUrl = created.openUrl,
                                                    statusUrl = created.statusUrl,
                                                    sending = false,
                                                    error = null
                                                )
                                            } else {
                                                it
                                            }
                                        }
                                        messages = next
                                        saveMessages(context, next)
                                        qrMessage = next.firstOrNull { it.localId == localMessage.localId }
                                    }
                                    .onFailure {
                                        val next = messages.map {
                                            if (it.localId == localMessage.localId) {
                                                it.copy(sending = false, error = "发送失败，请检查后端服务")
                                            } else {
                                                it
                                            }
                                        }
                                        messages = next
                                        saveMessages(context, next)
                                    }
                            }
                        }
                    )
                }
            }
        }
    }

    qrMessage?.let { message ->
        QrDialog(message = message, onDismiss = { qrMessage = null })
    }
}

@Composable
fun Header(title: String, showBack: Boolean, onBack: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(androidx.compose.ui.graphics.Color(0xFFF7F7F7))
            .padding(horizontal = 10.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (showBack) {
            Text(
                text = "<",
                color = androidx.compose.ui.graphics.Color(0xFF111111),
                fontSize = 28.sp,
                modifier = Modifier
                    .clickable(onClick = onBack)
                    .padding(horizontal = 10.dp)
            )
        }
        Text(
            text = title,
            color = androidx.compose.ui.graphics.Color(0xFF111111),
            fontSize = 18.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.weight(1f),
            textAlign = if (showBack) TextAlign.Start else TextAlign.Center
        )
        if (showBack) {
            Spacer(Modifier.width(48.dp))
        }
    }
    HorizontalDivider(color = androidx.compose.ui.graphics.Color(0xFFD6D6D6))
}

@Composable
fun ContactListScreen(
    contacts: List<Contact>,
    messages: List<ChatMessage>,
    newContactName: String,
    onNewContactNameChange: (String) -> Unit,
    onCreateContact: () -> Unit,
    onSelectContact: (Contact) -> Unit
) {
    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(androidx.compose.ui.graphics.Color.White)
                .padding(12.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = newContactName,
                onValueChange = onNewContactNameChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text("新建联系人") },
                singleLine = true
            )
            Button(
                onClick = onCreateContact,
                colors = ButtonDefaults.buttonColors(containerColor = androidx.compose.ui.graphics.Color(0xFF07C160)),
                contentPadding = PaddingValues(horizontal = 18.dp, vertical = 12.dp)
            ) {
                Text("添加")
            }
        }
        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(contacts) { contact ->
                val lastMessage = messages
                    .filter { it.contactId == contact.id }
                    .maxByOrNull { it.createdAt }
                ContactRow(contact = contact, lastMessage = lastMessage, onClick = { onSelectContact(contact) })
                HorizontalDivider(color = androidx.compose.ui.graphics.Color(0xFFE5E5E5))
            }
        }
    }
}

@Composable
fun ContactRow(contact: Contact, lastMessage: ChatMessage?, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(androidx.compose.ui.graphics.Color.White)
            .clickable(onClick = onClick)
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(46.dp)
                .background(androidx.compose.ui.graphics.Color(0xFF07C160), CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Text(contact.name.take(1), color = androidx.compose.ui.graphics.Color.White, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(contact.name, fontSize = 17.sp, color = androidx.compose.ui.graphics.Color(0xFF111111), fontWeight = FontWeight.Medium)
            Text(
                lastMessage?.body ?: "还没有消息，点击进入新建消息",
                fontSize = 13.sp,
                color = androidx.compose.ui.graphics.Color(0xFF888888),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Text(
            lastMessage?.let { formatTime(it.createdAt) } ?: "",
            color = androidx.compose.ui.graphics.Color(0xFFAAAAAA),
            fontSize = 11.sp
        )
    }
}

@Composable
fun ChatScreen(
    contact: Contact,
    messages: List<ChatMessage>,
    messageText: String,
    onMessageTextChange: (String) -> Unit,
    onShareMessage: (ChatMessage) -> Unit,
    onClick: () -> Unit
) {
    Column(modifier = Modifier.fillMaxSize()) {
        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .background(androidx.compose.ui.graphics.Color(0xFFEDEDED)),
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            items(messages) { message ->
                MessageBubble(message = message, onShare = { onShareMessage(message) })
            }
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(androidx.compose.ui.graphics.Color(0xFFF7F7F7))
                .navigationBarsPadding()
                .imePadding()
                .padding(8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = messageText,
                onValueChange = onMessageTextChange,
                modifier = Modifier.weight(1f),
                placeholder = { Text("输入消息") },
                minLines = 1,
                maxLines = 4
            )
            Button(
                onClick = onClick,
                enabled = messageText.trim().isNotEmpty(),
                colors = ButtonDefaults.buttonColors(containerColor = androidx.compose.ui.graphics.Color(0xFF07C160)),
                contentPadding = PaddingValues(horizontal = 18.dp, vertical = 12.dp)
            ) {
                Text("发送")
            }
        }
    }
}

@Composable
fun MessageBubble(message: ChatMessage, onShare: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.End
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.Top
        ) {
            Column(horizontalAlignment = Alignment.End) {
                Box(
                    modifier = Modifier
                        .widthIn(max = 280.dp)
                        .background(androidx.compose.ui.graphics.Color(0xFF95EC69), RoundedCornerShape(10.dp))
                        .padding(horizontal = 14.dp, vertical = 10.dp)
                ) {
                    Text(message.body, color = androidx.compose.ui.graphics.Color(0xFF111111), fontSize = 16.sp, lineHeight = 22.sp)
                }
                Row(
                    modifier = Modifier.padding(top = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    val statusText = when {
                        message.sending -> "发送中"
                        message.error != null -> message.error
                        message.read -> "已读 ${message.readAt?.let { formatTime(it) } ?: ""}".trim()
                        else -> "未读"
                    }
                    Text(
                        statusText,
                        color = if (message.read) {
                            androidx.compose.ui.graphics.Color(0xFF07C160)
                        } else {
                            androidx.compose.ui.graphics.Color(0xFF777777)
                        },
                        fontSize = 12.sp
                    )
                    TextButton(
                        enabled = message.openUrl != null,
                        onClick = onShare,
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp)
                    ) {
                        Text("分享", fontSize = 12.sp)
                    }
                }
            }
            Spacer(Modifier.width(8.dp))
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .background(androidx.compose.ui.graphics.Color(0xFF07C160), RoundedCornerShape(6.dp)),
                contentAlignment = Alignment.Center
            ) {
                Text("我", color = androidx.compose.ui.graphics.Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
fun QrDialog(message: ChatMessage, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val openUrl = message.openUrl ?: return
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = {
            Button(
                colors = ButtonDefaults.buttonColors(containerColor = androidx.compose.ui.graphics.Color(0xFF07C160)),
                onClick = {
                    val launchIntent = context.packageManager.getLaunchIntentForPackage("com.tencent.mm")
                    if (launchIntent != null) {
                        context.startActivity(launchIntent)
                    } else {
                        Toast.makeText(context, "未检测到微信", Toast.LENGTH_SHORT).show()
                    }
                }
            ) {
                Text("打开微信")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("关闭")
            }
        },
        title = { Text("消息二维码") },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Box(
                    modifier = Modifier
                        .background(androidx.compose.ui.graphics.Color.White, RoundedCornerShape(14.dp))
                        .padding(14.dp)
                ) {
                    Image(bitmap = qrBitmap(openUrl).asImageBitmap(), contentDescription = "二维码", modifier = Modifier.size(220.dp))
                }
                Text(
                    "此二维码请勿发送给别人\n请截图本二维码使用微信扫一扫\n打开后分享给好友",
                    color = androidx.compose.ui.graphics.Color(0xFF333333),
                    fontSize = 14.sp,
                    lineHeight = 22.sp,
                    textAlign = TextAlign.Center
                )
            }
        }
    )
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

fun loadContacts(context: Context): List<Contact> {
    val prefs = context.getSharedPreferences("wechat_state", Context.MODE_PRIVATE)
    val raw = prefs.getString("contacts", "[]") ?: "[]"
    val arr = JSONArray(raw)
    return (0 until arr.length()).mapNotNull { index ->
        val obj = arr.optJSONObject(index) ?: return@mapNotNull null
        Contact(
            id = obj.optString("id"),
            name = obj.optString("name"),
            createdAt = obj.optLong("createdAt")
        )
    }.filter { it.id.isNotBlank() && it.name.isNotBlank() }
}

fun saveContacts(context: Context, contacts: List<Contact>) {
    val arr = JSONArray()
    contacts.forEach {
        arr.put(
            JSONObject()
                .put("id", it.id)
                .put("name", it.name)
                .put("createdAt", it.createdAt)
        )
    }
    context.getSharedPreferences("wechat_state", Context.MODE_PRIVATE)
        .edit()
        .putString("contacts", arr.toString())
        .apply()
}

fun loadMessages(context: Context): List<ChatMessage> {
    val prefs = context.getSharedPreferences("wechat_state", Context.MODE_PRIVATE)
    val raw = prefs.getString("messages", "[]") ?: "[]"
    val arr = JSONArray(raw)
    return (0 until arr.length()).mapNotNull { index ->
        val obj = arr.optJSONObject(index) ?: return@mapNotNull null
        ChatMessage(
            localId = obj.optString("localId"),
            contactId = obj.optString("contactId"),
            body = obj.optString("body"),
            createdAt = obj.optLong("createdAt"),
            remoteId = obj.optString("remoteId").ifBlank { null },
            openUrl = obj.optString("openUrl").ifBlank { null },
            statusUrl = obj.optString("statusUrl").ifBlank { null },
            read = obj.optBoolean("read"),
            readAt = if (obj.isNull("readAt")) null else obj.optLong("readAt"),
            sending = obj.optBoolean("sending"),
            error = obj.optString("error").ifBlank { null }
        )
    }.filter { it.localId.isNotBlank() && it.contactId.isNotBlank() && it.body.isNotBlank() }
}

fun saveMessages(context: Context, messages: List<ChatMessage>) {
    val arr = JSONArray()
    messages.forEach {
        arr.put(
            JSONObject()
                .put("localId", it.localId)
                .put("contactId", it.contactId)
                .put("body", it.body)
                .put("createdAt", it.createdAt)
                .put("remoteId", it.remoteId)
                .put("openUrl", it.openUrl)
                .put("statusUrl", it.statusUrl)
                .put("read", it.read)
                .put("readAt", it.readAt)
                .put("sending", it.sending)
                .put("error", it.error)
        )
    }
    context.getSharedPreferences("wechat_state", Context.MODE_PRIVATE)
        .edit()
        .putString("messages", arr.toString())
        .apply()
}

fun formatTime(ms: Long): String {
    return SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(ms))
}
