package com.matrix.companion

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.matrix.companion.databinding.ActivityMainBinding
import com.matrix.companion.net.DeviceRegistrar
import com.matrix.companion.net.DeviceRegistrar.RegistrationState
import com.matrix.companion.service.CompanionService
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
        outcome.onSuccess { Logx.i("Pair OK") }
            .onFailure { Logx.e("Pair failed: ${it.message}") }
    }

    private fun App.deviceId(): String =
        getSharedPreferences("matrix_companion_meta", Context.MODE_PRIVATE)
            .getString("device_id", "(unset)") ?: "(unset)"

    private fun App.tailnetIp(): String? =
        try { com.matrix.companion.net.TailscaleClient.peekIp() } catch (_: Throwable) { null }
}
