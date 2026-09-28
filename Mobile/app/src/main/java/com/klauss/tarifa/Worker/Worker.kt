package com.klauss.tarifa

import java.time.LocalDate
import java.time.format.DateTimeFormatter
import kotlin.math.abs


// ACERTO DAS PREVISOES FEITAS ANTES DE CADA PRECO INFORMADO: QUANTOS PRECOS, QUANTOS TINHAM PREVISAO ANTERIOR, ERRO MEDIO E COBERTURA DA FAIXA
class Accuracy(val observed: Int = 0, val matched: Int = 0, val mae: Double = 0.0, val mape: Double = 0.0, val coverage: Double = 0.0)

// SERVICO EM SEGUNDO PLANO, COMO O WORKER DO DESKTOP: A CADA SLOT GUARDA A PREVISAO DAS ROTAS MONITORADAS E MEDE O ACERTO DELA CONTRA OS PRECOS REAIS INFORMADOS DEPOIS
object Worker {
    const val KEEP    = 7 * 86400L    // s de previsoes guardadas para comparar com precos informados
    const val TRACKED = 5             // rotas usadas mais recentes que sao monitoradas
    const val DELAY   = 5L            // s depois da virada do slot antes de observar
    const val SAMPLE  = 3             // precos informados com previsao anterior antes de mostrar a taxa de acerto; com menos ela diz pouco

    val COLUMNS = listOf("p10", "p50", "p90", "m10", "m50", "m90")

    @Volatile var status  = "Carregando a tabela de preços…"
    @Volatile var metrics = Accuracy()
    @Volatile var last    = 0L    // ultimo slot observado; zera quando entra rota nova, para ela ser monitorada ja

    // UM CICLO POR SLOT; A TELA E O ACOMPANHAMENTO CHAMAM A VONTADE E O SLOT JA OBSERVADO NAO SE REPETE
    fun handle(now: Long = System.currentTimeMillis() / 1000) = synchronized(this) {
        val slot = now / STEP * STEP

        if (!Oracle.ready() || slot <= last) return@synchronized

        val routes = Database.get("SELECT ${Database.COLUMNS} FROM Routes WHERE used_at > 0 ORDER BY used_at DESC LIMIT ?", TRACKED, fn = Database::getRoute)

        for (route in routes) {
            for (company in COMPANIES.keys) {
                val series = Engine.get(route, company, slot) ?: continue
                Database.set("INSERT OR IGNORE INTO Forecasts (route_id, company, made_at, ts, p10, p50, p90, m10, m50, m90) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (1 until series.ts.size).map { i -> arrayOf<Any?>(route.id, company, slot, series.ts[i], *COLUMNS.map { series.bands.getValue(it)[i] }.toTypedArray()) })
            }
        }

        check(slot)
        last = slot

        val found   = metrics
        val updated = LocalDate.parse(Oracle.MARKET!!.updated).format(DateTimeFormatter.ofPattern("dd/MM/yyyy"))
        val places  = "${routes.size} ${if (routes.size == 1) "rota monitorada" else "rotas monitoradas"}"
        val prices  = "${found.observed} ${if (found.observed == 1) "preço real informado" else "preços reais informados"}"
        val online  = if (found.matched >= SAMPLE) " · $prices: as previsões feitas antes erraram ${getFixed(found.mape * 100, 1)}% (${getMoney(found.mae)}) e a faixa acertou ${getFixed(found.coverage * 100, 0)}%" else if (found.observed > 0) " · $prices, comparando com as previsões anteriores (${found.matched} de $SAMPLE)" else " · informe o preço que o app mostra para medir o acerto"
        status = "Médias reais da Uber de $updated · $places$online"
    }

    // PRECOS INFORMADOS CONTRA AS PREVISOES FEITAS ANTES DELES PARA O MESMO SLOT, ROTA E APLICATIVO
    fun check(now: Long) {
        Database.set("DELETE FROM Forecasts WHERE ts < ?", listOf(arrayOf(now - KEEP)))
        val rows     = Database.get("SELECT fa.id, fa.observed, f.p10, f.p50, f.p90 FROM Fares fa JOIN Forecasts f ON f.route_id = fa.route_id AND f.company = fa.company AND f.ts = (fa.ts + ${STEP / 2}) / $STEP * $STEP AND f.made_at < fa.ts") { cursor -> cursor.getLong(0) to DoubleArray(4) { cursor.getDouble(it + 1) } }
        val observed = Database.get("SELECT COUNT(*) FROM Fares") { it.getInt(0) }.first()

        if (rows.isEmpty()) {
            metrics = Accuracy(observed)
            return
        }

        val error = rows.map { it.second[2] - it.second[0] }
        metrics = Accuracy(observed, rows.map { it.first }.distinct().size, error.map(::abs).average(), rows.indices.map { abs(error[it] / rows[it].second[0]) }.average(), rows.map { if (it.second[0] >= it.second[1] && it.second[0] <= it.second[3]) 1.0 else 0.0 }.average())
        Database.set("INSERT OR REPLACE INTO Metrics (ts, n, observed, mae, mape, coverage) VALUES (?, ?, ?, ?, ?, ?)", listOf(arrayOf<Any?>(now, rows.size, observed, metrics.mae, metrics.mape, metrics.coverage)))
    }
}
