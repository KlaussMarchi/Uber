package com.klauss.tarifa

import kotlin.math.abs


// ERRO MEDIO E COBERTURA DA FAIXA DAS PREVISOES CONSOLIDADAS DE UM ALVO
class Accuracy(val mae: Double, val coverage: Double)

// OBSERVACAO EM SEGUNDO PLANO, COMO O WORKER DO DESKTOP: ESTADO DO MERCADO A CADA SLOT NAS ROTAS RECENTES, PREVISOES DOS DOIS APLICATIVOS GUARDADAS E CONSOLIDADAS
object Worker {
    const val KEEP    = 7 * 86400L    // s de previsoes guardadas para consolidacao
    const val TRACKED = 5             // rotas recentes observadas pelo oraculo
    const val DELAY   = 5L            // s depois da virada do slot antes de observar
    const val SAMPLE  = 50            // previsoes consolidadas antes de mostrar a taxa de acerto; com menos ela oscila dezenas de pontos a cada ciclo

    val COLUMNS = listOf("p10", "p50", "p90", "m10", "m50", "m90")

    @Volatile var status  = "Carregando o modelo…"
    @Volatile var metrics = emptyMap<String, Accuracy>()
    @Volatile var samples = 0     // previsoes consolidadas nas ultimas 24 h
    @Volatile var last    = 0L    // ultimo slot observado; zera quando entra rota nova, para ela ser observada ja

    // UM CICLO POR SLOT; A TELA E O ACOMPANHAMENTO CHAMAM A VONTADE E O SLOT JA OBSERVADO NAO SE REPETE
    fun handle(now: Long = System.currentTimeMillis() / 1000) = synchronized(this) {
        val slot = now / STEP * STEP

        if (!Model.ready() || slot <= last) return@synchronized

        val routes = Database.get("SELECT id, origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, corridor, tz FROM Routes WHERE used_at > 0 ORDER BY used_at DESC LIMIT ?", TRACKED, fn = Database::getRoute)

        for (route in routes) {
            val series = COMPANIES.keys.mapNotNull { company -> Engine.get(route, company, slot)?.let { company to it } }

            if (series.size < COMPANIES.size) continue

            val state = series.first().second
            Database.set("INSERT OR IGNORE INTO Prices (route_id, ts, rain, excess, noise, minutes) VALUES (?, ?, ?, ?, ?, ?)", listOf(arrayOf<Any?>(route.id, slot, state.rain[0], state.excess, state.noise, state.minutes)))

            for ((company, found) in series) {
                Database.set("INSERT OR IGNORE INTO Forecasts (route_id, company, made_at, ts, p10, p50, p90, m10, m50, m90) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (1 until found.ts.size).map { i -> arrayOf<Any?>(route.id, company, slot, found.ts[i], *COLUMNS.map { found.bands.getValue(it)[i] }.toTypedArray()) })
            }
        }

        check(slot)
        last = slot

        val price  = metrics["p"]
        val time   = metrics["m"]
        val online = if (price == null || time == null || samples < SAMPLE) " · consolidando previsões ($samples de $SAMPLE)" else " · últimas 24 h: faixa acerta ${getFixed(price.coverage * 100, 0)}% no preço e ${getFixed(time.coverage * 100, 0)}% no tempo, erro de ${getMoney(price.mae)} e ${getFixed(time.mae, 1)} min"
        status = "Modelo v${Model.version} · ${routes.size} ${if (routes.size == 1) "rota monitorada" else "rotas monitoradas"}$online"
    }

    // PREVISOES CUJO HORARIO JA PASSOU CONTRA O ESTADO OBSERVADO NAQUELE SLOT; O PRECO DE CADA UMA SAI DA TARIFA DO SEU APLICATIVO
    fun check(now: Long) {
        Database.set("DELETE FROM Forecasts WHERE ts < ?", listOf(arrayOf(now - KEEP)))
        val rows = Database.get("SELECT f.company, f.p10, f.p50, f.p90, f.m10, f.m50, f.m90, p.excess, p.noise, p.minutes, r.distance FROM Forecasts f JOIN Prices p ON p.route_id = f.route_id AND p.ts = f.ts JOIN Routes r ON r.id = f.route_id WHERE f.ts > ?", now - 86400) { cursor ->
            val price = Oracle.getPrice(cursor.getDouble(10), cursor.getDouble(9), cursor.getDouble(7), cursor.getDouble(8), cursor.getString(0))
            doubleArrayOf(cursor.getDouble(1), cursor.getDouble(2), cursor.getDouble(3), price, cursor.getDouble(4), cursor.getDouble(5), cursor.getDouble(6), cursor.getDouble(9))
        }

        if (rows.isEmpty()) return

        samples = rows.size
        metrics = mapOf("p" to 0, "m" to 4).mapValues { (_, k) -> Accuracy(rows.map { abs(it[k + 3] - it[k + 1]) }.average(), rows.map { if (it[k + 3] >= it[k] && it[k + 3] <= it[k + 2]) 1.0 else 0.0 }.average()) }
        Database.set("INSERT OR REPLACE INTO Metrics (ts, version, n, price_mae, price_coverage, time_mae, time_coverage) VALUES (?, ?, ?, ?, ?, ?, ?)", listOf(arrayOf<Any?>(now, Model.version, rows.size, metrics.getValue("p").mae, metrics.getValue("p").coverage, metrics.getValue("m").mae, metrics.getValue("m").coverage)))
    }
}
