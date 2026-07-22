package com.matrix.companion

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
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
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

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

        binding.startServiceBtn.setOnClickListener {
            // Refuse to start the service if media access is missing —
            // publish-with-image would just UPLOAD_FAILED downstream and
            // the operator would have to re-pair to retry.
            if (!hasMediaPermission()) {
                Toast.makeText(
                    this,
                    "需要先授予图片读取权限，否则发布带图会失败",
                    Toast.LENGTH_LONG,
                ).show()
                requestMediaPermission()
                return@setOnClickListener
            }
            CompanionService.start(this)
            WatchdogService.start(this)
            Toast.makeText(this, "已启动", Toast.LENGTH_SHORT).show()
        }

        binding.stopServiceBtn.setOnClickListener {
            CompanionService.stop(this)
            Toast.makeText(this, "已停止", Toast.LENGTH_SHORT).show()
        }

        // Tail the global log buffer into the on-screen view.
        lifecycleScope.launch {
            while (true) {
                binding.logView.text = Logx.tail(200).joinToString("\n")
                kotlinx.coroutines.delay(750)
            }
        }

        observeRegistration()
        refreshStatus()
        handleIntentExtras(intent)
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
            }
        }
    }

    private fun refreshStatus() {
        val app = App.get(this)
        binding.deviceIdView.text = "device: ${app.deviceId()}"
        binding.tailnetIpView.text = "tailnet: ${app.tailnetIp() ?: "(offline)"}"
        binding.foregroundAppView.text = "app: ${app.statusProvider.foregroundApp() ?: "(unknown)"}"
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
