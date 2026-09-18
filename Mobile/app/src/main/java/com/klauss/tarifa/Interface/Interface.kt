package com.klauss.tarifa

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.CancellationSignal
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlin.coroutines.resume
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull


// cores validadas no desktop (banda de luminancia, daltonismo e contraste) contra o fundo dos cartoes
object COLORS {
    val background = Color(0xFF0B0D12)
    val card       = Color(0xFF141821)
    val input      = Color(0xFF1B2029)
    val hover      = Color(0xFF262C38)
    val border     = Color(0xFF2A303C)
    val grid       = Color(0xFF232833)
    val text       = Color(0xFFE8EAED)
    val secondary  = Color(0xFFC3C2B7)
    val muted      = Color(0xFF8B93A1)
    val price      = Color(0xFF3987E5)
    val traffic    = Color(0xFFD95926)
    val rain       = Color(0xFF199E70)
    val button     = Color(0xFF256ABF)
    val success    = Color(0xFF3DD68C)
    val error      = Color(0xFFFF6B6B)
}

// JANELA PRINCIPAL; REDE, BANCO E MODELO RODAM FORA DA THREAD DA TELA E SO O RESULTADO VOLTA PARA O ESTADO QUE O COMPOSE DESENHA
class Interface : ComponentActivity() {
    companion object {
        const val STATUS = 1000L       // ms entre atualizacoes da linha de status
        const val LOCATE = 10_000L     // ms de espera, somando todos os provedores, por uma leitura no nivel da rua; sem nenhuma leitura vale a estimativa por ip
        const val FRESH  = 120_000L    // ms de idade maxima da ultima posicao conhecida para valer sem leitura nova

        val STATS = listOf("distance" to "Distância", "corridor" to "Trecho na RJ-106", "duration" to "Tempo sem trânsito", "minutes" to "Tempo com trânsito agora", "rain" to "Chuva agora", "worst" to "Pior trânsito na janela", "peak" to "Pico de preço na janela", "low" to "Menor preço na janela")
    }

    val origin      = Search("ORIGEM", "Endereço de partida")
    val destination = Search("DESTINO", "Endereço de destino")
    val chart       = Chart()
    var company by mutableStateOf(COMPANIES.keys.first())
    var fare    by mutableStateOf("")
    var note    by mutableStateOf("")
    var busy    by mutableStateOf(false)
    var message by mutableStateOf("")
    var failed  by mutableStateOf(false)
    var status  by mutableStateOf(Worker.status)
    var live    by mutableStateOf(false)
    var tick    by mutableStateOf<Tick?>(null)
    var locator by mutableStateOf<Locator?>(null)
    var target: Search? = null

    val location = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted -> target?.let { getLocation(it, granted.values.any { value -> value }) } }
    val alerts   = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT), SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        Database.setup(this)

        lifecycleScope.launch(Dispatchers.IO) {
            Engine.setup()
            note = getHint()

            try {
                Model.setup(assets.open("model.json").bufferedReader().use { it.readText() })
            } catch (err: Exception) {
                Log.e("Interface", "modelo empacotado ilegivel", err)
                Worker.status = "Modelo empacotado ilegível: reinstale o app"
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                Tracker.resume(this@Interface)
                launch { Tracker.tick.collect { tick = it } }
                launch { while (true) { status = Worker.status; live = Tracker.running; delay(STATUS) } }
                launch(Dispatchers.IO) { handleWorker() }
            }
        }

        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = COLORS.button, background = COLORS.background, surface = COLORS.card, onSurface = COLORS.text)) {
                show()
            }
        }
    }

    // OBSERVACAO DAS ROTAS RECENTES A CADA SLOT ENQUANTO O APP ESTA ABERTO, COMO O WORKER DO DESKTOP; SEM MODELO AINDA, TENTA DE NOVO EM 1 S
    suspend fun handleWorker() {
        while (true) {
            try {
                Worker.handle()
            } catch (err: Exception) {
                Log.e("Interface", "ciclo do worker falhou", err)
                Worker.status = "Erro no ciclo em segundo plano; nova tentativa no próximo slot"
            }

            delay(if (Model.ready()) STEP * 1000 - System.currentTimeMillis() % (STEP * 1000) + Worker.DELAY * 1000 else STATUS)
        }
    }

    // LE OS CAMPOS NA THREAD DA TELA E PEDE AO ENGINE A CONSULTA COMPLETA EM SEGUNDO PLANO
    fun getForecast() {
        val src = origin.get()
        val dst = destination.get()

        if (busy) return

        if (src.label.isEmpty() || dst.label.isEmpty()) {
            message = "Preencha origem e destino."
            failed  = true
            return
        }

        busy    = true
        message = ""
        origin.hide()
        destination.hide()

        lifecycleScope.launch {
            val quote = withContext(Dispatchers.IO) {
                try {
                    Engine.getQuote(src, dst, company)
                } catch (err: Exception) {
                    Log.e("Interface", "consulta falhou", err)
                    Quote(error = "Falha inesperada na consulta; tente de novo.")
                }
            }

            showQuote(quote)
        }
    }

    // BOTAO ◎ DO CAMPO: PEDE A PERMISSAO UMA VEZ, LE O GPS (OU O IP, SEM PERMISSAO) E ABRE O MAPA DE QUALQUER JEITO PARA CONFIRMAR O PONTO EXATO
    fun getLocation(field: Search, granted: Boolean? = null) {
        val allowed = listOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION).any { ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED }

        if (!allowed && granted == null) {
            target = field
            location.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
            return
        }

        field.locating = true
        message        = "Localizando…"
        failed         = false

        lifecycleScope.launch {
            val position = if (allowed) getPosition() else null
            val place    = withContext(Dispatchers.IO) { if (position == null) Api.locate() else Api.reverse(position.latitude, position.longitude, position.accuracy <= Api.STREET).copy(accuracy = position.accuracy.toDouble()) }
            val recent   = withContext(Dispatchers.IO) { Engine.getPlaces(Locator.PLACES) }
            field.locating = false
            message = if (place == null) "Localização indisponível: marque o ponto no mapa." else "Confirme o ponto no mapa ou escolha um lugar já usado."
            locator = Locator(place, recent, { chosen -> showChoice(field, chosen) }, { locator = null })
        }
    }

    // POSICAO DO CELULAR: A ULTIMA CONHECIDA SE FOR RECENTE, SENAO LEITURAS NOVAS DE TODOS OS PROVEDORES AO MESMO TEMPO; VALE A PRIMEIRA NO NIVEL DA RUA OU, NO FIM DO PRAZO, A MAIS PRECISA
    @SuppressLint("MissingPermission")
    suspend fun getPosition(): Location? {
        val manager   = getSystemService(LocationManager::class.java)
        val providers = listOfNotNull(if (Build.VERSION.SDK_INT >= 31) LocationManager.FUSED_PROVIDER else null, LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER).filter(manager::isProviderEnabled)
        val recent    = providers.mapNotNull(manager::getLastKnownLocation).filter { System.currentTimeMillis() - it.time < FRESH }.minByOrNull { it.accuracy }
        val found     = mutableListOf<Location>()

        if (recent != null) return recent

        withTimeoutOrNull(LOCATE) {
            channelFlow {
                for (provider in providers) launch {
                    val position = suspendCancellableCoroutine<Location?> { cont ->
                        val signal = CancellationSignal()
                        cont.invokeOnCancellation { signal.cancel() }
                        LocationManagerCompat.getCurrentLocation(manager, provider, signal, ContextCompat.getMainExecutor(this@Interface)) { position -> cont.resume(position) }
                    }

                    if (position != null) send(position)
                }
            }.firstOrNull { found += it; it.accuracy <= Api.STREET }
        }

        return found.minByOrNull { it.accuracy }
    }

    // RESPOSTA DA CONSULTA DO USUARIO: MOTIVO DO ERRO OU ROTA VALIDADA, QUE PASSA A SER ACOMPANHADA EM TEMPO REAL
    fun showQuote(quote: Quote) {
        busy = false

        if (quote.error != null) {
            message = quote.error
            failed  = true
            return
        }

        origin.set(quote.src!!)
        destination.set(quote.dst!!)
        chart.index = null
        Tracker.start(this, quote.route!!, quote.series!!, company)

        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) alerts.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    // TROCA DE APLICATIVO: A ROTA NA TELA E RECALCULADA COM A TARIFA E A DINAMICA DO ESCOLHIDO
    fun handleCompany(chosen: String) {
        company = chosen
        note    = getHint()
        val current = tick ?: return

        lifecycleScope.launch {
            val series = withContext(Dispatchers.IO) { Engine.get(current.route, chosen) }
            if (series != null && chosen == company) Tracker.start(this@Interface, current.route, series, chosen)
        }
    }

    // PRECO REAL LIDO NO APLICATIVO: AJUSTA A TARIFA DELE E RECALCULA A SERIE; ZERO APAGA A CALIBRACAO
    fun handleFare() {
        val current = tick
        val chosen  = company
        val typed   = fare.trim().removePrefix("R$").trim()
        val value   = (if (',' in typed) typed.replace(".", "").replace(",", ".") else typed).toDoubleOrNull()    // 1.234,56 e 1234.56 valem o mesmo

        if (current == null || value == null || value < 0) {
            message = "Calcule um preço e informe o valor real do aplicativo, como 54,85 (0 apaga a calibração)."
            failed  = true
            return
        }

        fare    = ""
        message = "Calibrando a tarifa…"
        failed  = false

        lifecycleScope.launch {
            val count = withContext(Dispatchers.IO) { Engine.setFare(current.route, chosen, value) }

            if (count < 0) {
                message = "Não foi possível calibrar agora: a previsão desta rota está indisponível."
                failed  = true
                return@launch
            }

            val series = withContext(Dispatchers.IO) { Engine.get(current.route, company) }
            Tracker.reset(current.route, chosen)
            note    = getHint()
            message = if (count > 0) "Tarifa da ${COMPANIES.getValue(chosen)} ajustada a $count preço(s) informado(s)." else "Calibração da ${COMPANIES.getValue(chosen)} apagada: voltou à tabela publicada."
            if (series != null) Tracker.start(this@Interface, current.route, series, company)
        }
    }

    // ORIGEM DA TARIFA EM USO, SOB O CAMPO DE PRECO REAL
    fun getHint(): String {
        val fare  = Oracle.getFare(company)
        val state = if (fare == Oracle.TARIFFS.getValue(company)) "tabela publicada" else "calibrada em R$ ${getFixed(fare.km, 2)}/km e R$ ${getFixed(fare.minute, 2)}/min"
        return "Tarifa ${COMPANIES.getValue(company)}: $state"
    }

    // PONTO ESCOLHIDO NO MAPA OU NA LISTA: VIRA O ENDERECO DO CAMPO
    fun showChoice(field: Search, place: Place) {
        field.set(place)
        message = "Ponto definido: ${place.label}"
        failed  = false
        locator = null
    }

    @Composable
    fun showPanel(content: @Composable ColumnScope.() -> Unit) = Column(Modifier.fillMaxWidth().background(COLORS.card, RoundedCornerShape(16.dp)).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)

    @Composable
    fun show() {
        Column(Modifier.fillMaxSize().background(COLORS.background).safeDrawingPadding().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Tarifa Dinâmica", color = COLORS.text, fontSize = 22.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text("●", color = if (Model.ready()) COLORS.success else COLORS.muted, fontSize = 14.sp)
            }

            Text("previsão de preço de corrida por aplicativo", color = COLORS.muted, fontSize = 13.sp)
            Text(status, color = COLORS.secondary, fontSize = 12.sp)

            showPanel {
                Row(Modifier.fillMaxWidth().background(COLORS.input, RoundedCornerShape(10.dp)).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    for ((key, name) in COMPANIES) {
                        Button(onClick = { handleCompany(key) }, modifier = Modifier.weight(1f).height(42.dp), shape = RoundedCornerShape(8.dp), contentPadding = PaddingValues(0.dp), colors = ButtonDefaults.buttonColors(containerColor = if (key == company) COLORS.button else COLORS.input, contentColor = if (key == company) Color.White else COLORS.muted)) {
                            Text(name, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }

                Text(note, color = COLORS.muted, fontSize = 11.sp)
                origin.show(::getForecast) { getLocation(origin) }
                destination.show(::getForecast) { getLocation(destination) }

                Button(onClick = ::getForecast, enabled = !busy, modifier = Modifier.fillMaxWidth().height(50.dp), shape = RoundedCornerShape(10.dp), colors = ButtonDefaults.buttonColors(containerColor = COLORS.button, contentColor = Color.White, disabledContainerColor = COLORS.input, disabledContentColor = COLORS.secondary)) {
                    Text(if (busy) "Calculando…" else "Calcular preço", fontSize = 16.sp, fontWeight = FontWeight.Bold)
                }

                if (message.isNotEmpty()) Text(message, color = if (failed) COLORS.error else COLORS.muted, fontSize = 12.sp)
            }

            val current = tick

            if (current == null) {
                showPanel { Text("Informe origem e destino e toque em Calcular preço.", color = COLORS.muted, fontSize = 13.sp) }
            } else {
                showPrice(current)
                chart.show(current)
            }

            Text("Preços simulados sobre a tarifa de cada aplicativo; informe o preço real do app para calibrar. Endereços (OpenStreetMap), rotas (OSRM) e clima (Open-Meteo) são dados reais.", color = COLORS.muted, fontSize = 11.sp)
        }

        locator?.show()
    }

    // PRECO OBSERVADO EM DESTAQUE COM A VARIACAO DESDE A PRIMEIRA CONSULTA E O RESUMO DA JANELA ESCOLHIDA NO GRAFICO
    @Composable
    fun showPrice(tick: Tick) {
        val route  = tick.route
        val series = tick.series
        val bands  = series.bands
        val view   = series.ts.indices.filter { series.ts[it] <= series.ts[0] + chart.hours * 3600 }
        val peak   = view.maxBy { bands.getValue("p50")[it] }
        val low    = view.minBy { bands.getValue("p50")[it] }
        val worst  = view.maxBy { bands.getValue("m50")[it] }
        val clock  = { i: Int -> getLocal(series.ts[i], route.tz).format(Chart.HHMM) }
        val (way, change) = Tracker.getChange(route, tick.company, series.price)
        val color  = listOf(COLORS.price, COLORS.text, COLORS.error)[way + 1]

        val values = mapOf(
            "distance" to "${getFixed(route.distance, 1)} km",
            "corridor" to "${getFixed(route.corridor * 100, 0)}%",
            "duration" to getDuration(route.duration),
            "minutes"  to "${getDuration(series.minutes)} · ${getDelay(series.minutes - route.duration)}",
            "rain"     to "${getFixed(series.rain[0], 1)} mm/h · ${getFixed(series.probability[0], 0)}%",
            "worst"    to "${getDuration(bands.getValue("m50")[worst])} · ${clock(worst)}",
            "peak"     to "${getMoney(bands.getValue("p50")[peak])} · ${clock(peak)}",
            "low"      to "${getMoney(bands.getValue("p50")[low])} · ${clock(low)}",
        )

        showPanel {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("PREÇO AGORA · ${COMPANIES.getValue(tick.company)}", color = COLORS.muted, fontSize = 12.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text("●", color = if (live) COLORS.success else COLORS.muted, fontSize = 12.sp)
                Spacer(Modifier.width(6.dp))
                Text(if (live) "tempo real · ${Tracker.SYNC / 1000} s" else "tempo real pausado", color = COLORS.muted, fontSize = 12.sp)
            }

            Column {
                Text(getMoney(series.price), color = color, fontSize = 40.sp, fontWeight = FontWeight.Bold)
                Text("$change · atualizado ${tick.at}", color = color, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                Text("próximos 10 min entre ${getMoney(bands.getValue("p10")[1])} e ${getMoney(bands.getValue("p90")[1])} · viagem de ${getSpan(bands.getValue("m10")[1], bands.getValue("m90")[1])}", color = COLORS.secondary, fontSize = 12.sp)
            }

            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value           = fare,
                    onValueChange   = { fare = it },
                    modifier        = Modifier.weight(1f),
                    singleLine      = true,
                    placeholder     = { Text("preço real no app", color = COLORS.muted, fontSize = 14.sp) },
                    textStyle       = TextStyle(color = COLORS.text, fontSize = 14.sp),
                    shape           = RoundedCornerShape(8.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { handleFare() }),
                    colors          = OutlinedTextFieldDefaults.colors(focusedContainerColor = COLORS.input, unfocusedContainerColor = COLORS.input, focusedBorderColor = COLORS.button, unfocusedBorderColor = COLORS.border, cursorColor = COLORS.text, focusedTextColor = COLORS.text, unfocusedTextColor = COLORS.text),
                )

                OutlinedButton(onClick = ::handleFare, shape = RoundedCornerShape(8.dp), border = BorderStroke(1.dp, COLORS.border), colors = ButtonDefaults.outlinedButtonColors(containerColor = COLORS.input, contentColor = COLORS.text)) {
                    Text("Calibrar", fontWeight = FontWeight.Bold)
                }
            }

            for (row in STATS.chunked(2)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    for ((key, title) in row) {
                        Column(Modifier.weight(1f).background(COLORS.input, RoundedCornerShape(10.dp)).padding(horizontal = 12.dp, vertical = 8.dp)) {
                            Text(title, color = COLORS.muted, fontSize = 11.sp)
                            Text(values.getValue(key), color = COLORS.text, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }
    }
}
