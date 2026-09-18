package com.klauss.tarifa

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min


// OBSERVACAO MAIS RECENTE DA ROTA ACOMPANHADA, NO APLICATIVO ESCOLHIDO, E A HORA DO CELULAR EM QUE ELA CHEGOU
class Tick(val route: Route, val series: Series, val at: String, val company: String)

// TEMPO REAL DA ROTA NA TELA, TAMBEM COM O APP FECHADO: A CADA 30 S OBSERVA O PRECO, ATUALIZA A NOTIFICACAO E TOCA O ALERTA A CADA 10% DE AFASTAMENTO
class Tracker : Service() {
    companion object {
        const val SYNC     = 30_000L          // ms entre observacoes do preco em tempo real
        const val ALERT    = 0.10             // cada 10% de afastamento desde a primeira consulta toca um alerta; no mesmo patamar nao repete
        const val LIFETIME = 3 * 3600_000L    // ms desde a ultima consulta ate o acompanhamento parar sozinho, para poupar bateria
        const val ONGOING  = 1
        const val WARNING  = 2
        const val STOP     = "parar"
        const val LIVE     = "tempo-real"
        const val ALERTS   = "alertas"

        val tick      = MutableStateFlow<Tick?>(null)
        val baselines = HashMap<Pair<Long, String>, Pair<Double, String>>()    // preco e hora da primeira consulta de cada rota e aplicativo nesta sessao
        val levels    = HashMap<Pair<Long, String>, Int>()

        @Volatile var route: Route? = null
        @Volatile var company = COMPANIES.keys.first()
        @Volatile var started = 0L
        @Volatile var running = false

        // CONSULTA NOVA NA TELA (OU TROCA DE APLICATIVO): VIRA A ROTA ACOMPANHADA, FIXA O PRECO DE REFERENCIA NA PRIMEIRA VEZ E GARANTE O SERVICO RODANDO
        fun start(context: Context, route: Route, series: Series, company: String) {
            synchronized(baselines) {
                baselines.getOrPut(route.id to company) { series.price to getNow("HH:mm") }
                levels.getOrPut(route.id to company) { 0 }
            }

            Tracker.route   = route
            Tracker.company = company
            started     = System.currentTimeMillis()
            tick.value  = Tick(route, series, getNow("HH:mm:ss"), company)
            Worker.last = 0
            resume(context)
        }

        // RELIGA O SERVICO DA ROTA QUE JA ESTAVA NA TELA (APP REABERTO DEPOIS DE PARAR PELA NOTIFICACAO OU PELO LIMITE DE TEMPO)
        fun resume(context: Context) {
            if (route == null || running) return
            started = System.currentTimeMillis()

            // com o app ja em segundo plano (consulta que terminou depois de sair da tela) o android recusa o servico; ele volta quando a tela abrir
            try {
                context.startForegroundService(Intent(context, Tracker::class.java))
            } catch (err: IllegalStateException) {
                Log.w("Tracker", "acompanhamento nao iniciado com o app em segundo plano: $err")
            }
        }

        // PATAMAR DE 10% DO PRECO ATUAL EM RELACAO A PRIMEIRA CONSULTA DAQUELE APLICATIVO; DEVOLVE O ALERTA QUANDO CRUZA UM PATAMAR NOVO
        fun check(route: Route, company: String, price: Double): String? = synchronized(baselines) {
            val base  = baselines.getValue(route.id to company).first
            val level = levels.getValue(route.id to company)
            val band  = ((price / base - 1) / ALERT).toInt()
            levels[route.id to company] = band
            if (band < min(level, 0)) "good" else if (band > max(level, 0)) "bad" else null
        }

        // SENTIDO (-1, 0, 1) E TEXTO DA VARIACAO DESDE A PRIMEIRA CONSULTA; ABAIXO DE 0,05% ELA SOME NO ARREDONDAMENTO MOSTRADO
        fun getChange(route: Route, company: String, price: Double): Pair<Int, String> {
            val (base, since) = synchronized(baselines) { baselines[route.id to company] } ?: return 0 to ""
            val change = price / base - 1
            val way    = if (abs(change) < 0.0005) 0 else if (change < 0) -1 else 1
            return way to "${listOf("▼", "•", "▲")[way + 1]} ${getFixed(abs(change) * 100, 1)}% desde $since"
        }

        // TARIFA RECEM-CALIBRADA: A REFERENCIA DA VARIACAO RECOMECA, PORQUE O PRECO MUDOU DE NIVEL
        fun reset(route: Route, company: String) = synchronized(baselines) {
            baselines.remove(route.id to company)
            levels.remove(route.id to company)
        }

        fun getNow(pattern: String): String = LocalTime.now().format(DateTimeFormatter.ofPattern(pattern))

        fun getName(label: String) = label.substringBefore(',')
    }

    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    var job: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Database.setup(this)
        Engine.setup()    // a notificacao mostra preco: a tarifa calibrada tem de estar carregada mesmo se o servico subir antes da tela
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(LIVE, "Preço em tempo real", NotificationManager.IMPORTANCE_LOW))
        manager.createNotificationChannel(NotificationChannel(ALERTS, "Alertas de preço", NotificationManager.IMPORTANCE_HIGH).apply { setSound(null, null) })
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }

        if (Build.VERSION.SDK_INT >= 34) startForeground(ONGOING, getNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE) else startForeground(ONGOING, getNotification())
        running = true

        if (job?.isActive != true) job = scope.launch { handleThread() }
        return START_NOT_STICKY
    }

    // LACO DO ACOMPANHAMENTO: OBSERVA JA E DEPOIS A CADA 30 S; UM ERRO NUM CICLO VAI PARA O LOG E O CICLO SEGUINTE TENTA DE NOVO
    suspend fun handleThread() {
        val manager = getSystemService(NotificationManager::class.java)

        while (System.currentTimeMillis() - started < LIFETIME) {
            val current = route ?: break

            try {
                Worker.handle()
                val chosen = company
                val series = Engine.get(current, chosen)

                if (series != null && current.id == route?.id && chosen == company) {
                    tick.value = Tick(current, series, getNow("HH:mm:ss"), chosen)
                    manager.notify(ONGOING, getNotification())
                    check(current, chosen, series.price)?.let { kind ->
                        play(kind)
                        manager.notify(WARNING, getWarning(current, chosen, series, kind))
                    }
                }
            } catch (err: Exception) {
                Log.e("Tracker", "ciclo do acompanhamento falhou", err)
            }

            delay(SYNC)
        }

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // NOTIFICACAO FIXA COM O PRECO DE AGORA, A VARIACAO E O BOTAO PARAR; TOCAR NELA ABRE O APP
    fun getNotification(): Notification {
        val current = tick.value
        val open    = PendingIntent.getActivity(this, 0, Intent(this, Interface::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        val stop    = PendingIntent.getService(this, 1, Intent(this, Tracker::class.java).setAction(STOP), PendingIntent.FLAG_IMMUTABLE)
        val title   = if (current == null) "Tarifa Dinâmica" else "${COMPANIES.getValue(current.company)} · ${getMoney(current.series.price)} ${getChange(current.route, current.company, current.series.price).second.substringBefore(" desde")}"
        val text    = if (current == null) "Acompanhando o preço" else "${getName(current.route.origin)} → ${getName(current.route.destination)} · atualizado ${current.at}"
        return Notification.Builder(this, LIVE).setSmallIcon(R.drawable.ic_notification).setContentTitle(title).setContentText(text).setOngoing(true).setOnlyAlertOnce(true).setContentIntent(open).addAction(Notification.Action.Builder(null, "Parar", stop).build()).build()
    }

    // ALERTA DE PATAMAR CRUZADO, COM O PRECO NOVO E A VARIACAO DESDE A PRIMEIRA CONSULTA
    fun getWarning(route: Route, company: String, series: Series, kind: String): Notification {
        val open = PendingIntent.getActivity(this, 2, Intent(this, Interface::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        val verb = if (kind == "good") "caiu" else "subiu"
        return Notification.Builder(this, ALERTS).setSmallIcon(R.drawable.ic_notification).setContentTitle("${COMPANIES.getValue(company)}: preço $verb para ${getMoney(series.price)}").setContentText("${getChange(route, company, series.price).second} · ${getName(route.origin)} → ${getName(route.destination)}").setAutoCancel(true).setContentIntent(open).build()
    }

    override fun onDestroy() {
        running = false
        scope.cancel()
        super.onDestroy()
    }
}
