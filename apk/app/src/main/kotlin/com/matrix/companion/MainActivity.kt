package com.matrix.companion

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.matrix.companion.databinding.ActivityMainBinding
import com.matrix.companion.net.DeviceRegistrar
import com.matrix.companion.net.DeviceRegistrar.RegistrationState
import com.matrix.companion.net.MasterConfig
import com.matrix.companion.service.CompanionService
import com.matrix.companion.service.TaskPollerService
import com.matrix.companion.service.WatchdogService
import com.matrix.companion.util.Logx
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.get
import io.ktor.client.statement.bodyAsText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * First-run / debug entry. Three jobs:
 *   1. Prompt user to enable the accessibility service.
 *   2. Accept a pairing code issued by the main controller; send it to
 *      [DeviceRegistrar.pair] to bootstrap the HMAC secret.
 *   3. Start [CompanionService] which runs the long-lived HTTP server.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val registrar by lazy { DeviceRegistrar(this) }
    private val healthClient = HttpClient(CIO) {
        install(HttpTimeout) {
            requestTimeoutMillis = 10000
            connectTimeoutMillis = 5000
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.enableAccessibilityBtn.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        binding.pairBtn.setOnClickListener {
            val code = binding.pairCodeInput.text?.toString().orEmpty().trim()
            if (code.length < 4) {
                Toast.makeText(this, "配对码长度不对", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            lifecycleScope.launch { runPair(code) }
        }

        // 自动启动服务（打开 App 即启动，无需手动点按钮）
        if (hasMediaPermission()) {
            CompanionService.start(this)
            WatchdogService.start(this)
        } else {
            // 没权限时提示并请求
            Toast.makeText(
                this,
                "需要图片读取权限才能自动启动服务",
                Toast.LENGTH_LONG,
            ).show()
            requestMediaPermission()
        }

        // Tail the global log buffer into the on-screen view.
        lifecycleScope.launch {
            while (isActive) {
                binding.logView.text = Logx.tail(200).joinToString("\n")
                delay(750)
            }
        }

        observeRegistration()
        refreshStatus()
        handleIntentExtras(intent)
        startMasterHealthCheck()
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        handleIntentExtras(intent)
    }

    private fun handleIntentExtras(intent: Intent?) {
        intent?.getStringExtra("master_url")?.let { url ->
            if (url.startsWith("http://") || url.startsWith("https://")) {
                MasterConfig.set(this, url)
                Logx.i("MainActivity.master_url_override: $url")
            }
        }
        intent?.getStringExtra("pair_code")?.let { code ->
            if (code.length >= 4) {
                lifecycleScope.launch { runPair(code) }
            }
        }
        if (intent?.getBooleanExtra("start_service", false) == true) {
            CompanionService.start(this)
            WatchdogService.start(this)
            TaskPollerService.start(this)
            Toast.makeText(this, "已启动", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    override fun onDestroy() {
        super.onDestroy()
        healthClient.close()
    }

    private fun observeRegistration() {
        lifecycleScope.launch {
            registrar.state.collectLatest { state ->
                binding.pairBtn.isEnabled = state is RegistrationState.Idle
                binding.statusTitle.text = when (state) {
                    is RegistrationState.Idle -> getString(R.string.status_idle)
                    is RegistrationState.Registering -> getString(R.string.status_pairing)
                    is RegistrationState.Paired -> getString(R.string.status_paired)
                    is RegistrationState.Failed -> "配对失败：${state.reason}"
                }
                updatePairStatus(state)
            }
        }
    }

    private fun updatePairStatus(state: RegistrationState? = null) {
        val app = App.get(this)
        val provisioned = app.hmacSecretStore.isProvisioned()
        val effectiveState = state ?: if (provisioned) RegistrationState.Paired else RegistrationState.Idle

        binding.pairStatusView.text = when (effectiveState) {
            is RegistrationState.Paired -> "配对：已配对"
            is RegistrationState.Registering -> "配对：配对中…"
            is RegistrationState.Failed -> "配对：失败（${effectiveState.reason}）"
            else -> "配对：未配对"
        }
        binding.pairStatusView.setTextColor(
            when (effectiveState) {
                is RegistrationState.Paired -> Color.parseColor("#2E7D32")
                is RegistrationState.Failed -> Color.parseColor("#C62828")
                else -> Color.parseColor("#616161")
            }
        )
    }

    private fun refreshStatus() {
        val app = App.get(this)
        binding.deviceIdView.text = "device: ${app.deviceId()}"
        binding.tailnetIpView.text = "tailnet: ${app.tailnetIp() ?: "(offline)"}"
        binding.foregroundAppView.text = "app: ${app.statusProvider.foregroundApp() ?: "(unknown)"}"

        // 配对状态
        updatePairStatus()

        // 无障碍状态
        val accessibilityOn = App.accessibilityServiceInstance != null
        binding.accessibilityStatusView.text = if (accessibilityOn) "无障碍：已开启" else "无障碍：未开启"
        binding.accessibilityStatusView.setTextColor(
            if (accessibilityOn) Color.parseColor("#2E7D32") else Color.parseColor("#C62828")
        )

        // Tailscale 状态
        val tsState = com.matrix.companion.net.TailscaleClient.status()
        val tsOn = tsState == "connected"
        binding.tailscaleStatusView.text = "Tailscale：$tsState"
        binding.tailscaleStatusView.setTextColor(
            if (tsOn) Color.parseColor("#2E7D32") else Color.parseColor("#C62828")
        )

        // 服务状态（根据是否有 media 权限判断能否启动）
        val serviceOn = hasMediaPermission()
        binding.serviceStatusView.text = if (serviceOn) "服务：自动运行中" else "服务：未授权图片权限"
        binding.serviceStatusView.setTextColor(
            if (serviceOn) Color.parseColor("#2E7D32") else Color.parseColor("#C62828")
        )
    }

    private fun startMasterHealthCheck() {
        lifecycleScope.launch {
            while (isActive) {
                checkMasterHealth()
                delay(5000)
            }
        }
    }

    private suspend fun checkMasterHealth() {
        val masterUrl = MasterConfig.get(this)
        try {
            val resp = withContext(Dispatchers.IO) {
                healthClient.get("$masterUrl/api/v1/health")
            }
            val body = resp.bodyAsText()
            val ok = resp.status.value in 200..299 && body.contains("\"status\":\"ok\"")
            Logx.i("master_health: status=${resp.status.value} ok=$ok")
            withContext(Dispatchers.Main) {
                binding.masterStatusView.text = if (ok) "主控：在线" else "主控：异常"
                binding.masterStatusView.setTextColor(
                    if (ok) Color.parseColor("#2E7D32") else Color.parseColor("#C62828")
                )
            }
        } catch (e: Exception) {
            Logx.w("master_health_failed: ${e.message}")
            withContext(Dispatchers.Main) {
                binding.masterStatusView.text = "主控：离线"
                binding.masterStatusView.setTextColor(Color.parseColor("#C62828"))
            }
        }
    }

    private suspend fun runPair(code: String) {
        val outcome = registrar.pair(code)
        refreshStatus()
        outcome.onSuccess {
            Logx.i("Pair OK")
            Toast.makeText(this@MainActivity, "配对成功！", Toast.LENGTH_SHORT).show()
        }.onFailure { err ->
            Logx.e("Pair failed: ${err.message}")
            Toast.makeText(
                this@MainActivity,
                "配对失败：${err.message ?: "未知错误"}",
                Toast.LENGTH_LONG,
            ).show()
        }
    }

    private fun App.deviceId(): String =
        getSharedPreferences("matrix_companion_meta", Context.MODE_PRIVATE)
            .getString("device_id", "(unset)") ?: "(unset)"

    private fun App.tailnetIp(): String? =
        try { com.matrix.companion.net.TailscaleClient.peekIp() } catch (_: Throwable) { null }

    // ----- Media permission helpers (Phase 1 image upload) -----

    /**
     * The permission name changed across Android versions:
     * - API 33+ (Android 13+): READ_MEDIA_IMAGES — scoped media access.
     * - API 29–32: READ_EXTERNAL_STORAGE — broad storage read.
     * - API ≤28: same READ_EXTERNAL_STORAGE works, scoped to legacy mode.
     */
    private val mediaPermissionName: String
        get() = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            Manifest.permission.READ_MEDIA_IMAGES
        } else {
            Manifest.permission.READ_EXTERNAL_STORAGE
        }

    private fun hasMediaPermission(): Boolean = ContextCompat.checkSelfPermission(
        this, mediaPermissionName,
    ) == PackageManager.PERMISSION_GRANTED

    private val mediaPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            Logx.i("media_permission.granted")
        } else {
            Logx.w("media_permission.denied — publish-with-image will fail")
            Toast.makeText(this, "未授予图片权限", Toast.LENGTH_SHORT).show()
        }
    }

    private fun requestMediaPermission() {
        mediaPermissionLauncher.launch(mediaPermissionName)
    }
}
