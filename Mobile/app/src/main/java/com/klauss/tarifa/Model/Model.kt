package com.klauss.tarifa

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt


// FAIXAS P10/P50/P90 DO PRECO E M10/M50/M90 DO TEMPO: MISTURA DO CENARIO SECO E DO CHUVOSO PELA CHANCE DE CHUVA, COM A INCERTEZA DO NIVEL DA REGIAO, DA DINAMICA E DO TRANSITO; A MESMA CONTA DO MODEL/INDEX.PY
object Model {
    val QUANTILES = linkedMapOf("10" to 0.10, "50" to 0.50, "90" to 0.90)
    val DIGITS    = mapOf("p" to 2, "m" to 2)    // centavos e centesimo de minuto, para a faixa nao empatar em viagem curta
    val ERFC      = doubleArrayOf(-1.26551223, 1.00002368, 0.37409196, 0.09678418, -0.18628806, 0.27886807, -1.13520398, 1.48851587, -0.82215223, 0.17087277)

    const val MIN_BAND     = 0.01    // largura minima da faixa, fracao da mediana
    const val DYNAMIC_PEAK = 0.15    // desvio a mais por unidade de excesso de demanda (pico, chuva)
    const val TIME_BASE    = 0.06    // desvio log do tempo de viagem de madrugada
    const val TIME_PEAK    = 0.04    // desvio a mais por unidade de congestionamento relativo
    const val TIME_RAIN    = 0.10    // desvio a mais com chuva forte
    const val RAIN_SURE    = 0.1     // mm/h previstos a partir dos quais a chuva conta como provavel mesmo com chance baixa
    const val RAIN_LIGHT   = 0.5     // mm/h minimos do cenario chuvoso
    const val RAIN_HEAVY   = 30.0
    const val SIGMA_MIN    = 1e-4
    const val STEPS        = 60      // bissecoes do quantil; 2^-60 da faixa inicial ja e menor que um centavo

    // DISTRIBUICAO NORMAL ACUMULADA PELA ERFC DO NUMERICAL RECIPES (ERRO RELATIVO ABAIXO DE 1,2E-7)
    fun getNormal(z: Double): Double {
        val x    = -z / sqrt(2.0)
        val t    = 1 / (1 + 0.5 * abs(x))
        var poly = ERFC[9]

        for (k in 8 downTo 0) poly = ERFC[k] + t * poly

        val r = t * exp(-x * x + poly)
        return 0.5 * (if (x >= 0) r else 2 - r)
    }

    // QUANTIL DE UMA MISTURA DE DUAS NORMAIS (SECO E CHUVOSO) POR BISSECAO; SEM CHUVA POSSIVEL VIRA A NORMAL DO CENARIO SECO
    fun getQuantile(q: Double, wet: Double, means: DoubleArray, sigmas: DoubleArray): Double {
        var lo = min(means[0] - 8 * sigmas[0], means[1] - 8 * sigmas[1])
        var hi = max(means[0] + 8 * sigmas[0], means[1] + 8 * sigmas[1])

        repeat(STEPS) {
            val mid = (lo + hi) / 2
            if ((1 - wet) * getNormal((mid - means[0]) / sigmas[0]) + wet * getNormal((mid - means[1]) / sigmas[1]) < q) lo = mid else hi = mid
        }

        return (lo + hi) / 2
    }

    // CENARIOS DE CHUVA: A CHANCE DO OPEN-METEO E O PESO DO CHUVOSO, E A CHUVA DELE E A PREVISTA DIVIDIDA PELA CHANCE (A PREVISAO JA E A MEDIA)
    fun getScenario(rain: Double, probability: Double): Pair<Double, Double> {
        val chance = (if (probability.isNaN()) 0.0 else probability / 100).coerceIn(0.0, 1.0)
        val wet    = if (rain > RAIN_SURE) max(chance, 0.5) else chance
        return wet to (rain / max(wet, 1e-9)).coerceIn(RAIN_LIGHT, RAIN_HEAVY)
    }

    // FAIXAS NOS INSTANTES PEDIDOS, SEM ARREDONDAR; O PRECO INFORMADO MAIS RECENTE PUXA O COMECO DA SERIE E SE DISSIPA
    fun getBands(route: Route, ts: LongArray, rain: DoubleArray, probability: DoubleArray, company: String, calibration: Calibration): Map<String, DoubleArray> {
        val n         = ts.size
        val scenarios = List(n) { getScenario(rain[it], probability[it]) }
        val wet       = DoubleArray(n) { scenarios[it].first }
        val heavy     = DoubleArray(n) { scenarios[it].second }
        val error     = Oracle.MARKET!!.sigma.getDouble("pace")    // desvio log do ritmo medio da rota, que o preco informado nao corrige
        val near      = DoubleArray(n) { if (ts[it] >= calibration.ts) calibration.weight * exp(-max(ts[it] - calibration.ts - Oracle.HOLD, 0L).toDouble() / Oracle.TAU) else 0.0 }
        val means     = mapOf("p" to Array(2) { DoubleArray(n) }, "m" to Array(2) { DoubleArray(n) })
        val sigmas    = mapOf("p" to Array(2) { DoubleArray(n) }, "m" to Array(2) { DoubleArray(n) })

        for ((s, scenario) in listOf(DoubleArray(n), heavy).withIndex()) {
            val state = Oracle.getState(route, ts, scenario)

            for (i in 0 until n) {
                val drops   = 1 - exp(-scenario[i] / Oracle.RAIN_MM)
                val dynamic = Oracle.DYNAMIC + DYNAMIC_PEAK * state.excess[i]
                val travel  = TIME_BASE + TIME_PEAK * state.traffic[i] + TIME_RAIN * drops
                val share   = Oracle.getShare(route.distance, state.minutes[i]) * travel
                val spread  = (1 - near[i]) * (1 - near[i]) * calibration.variance + (1 - near[i] * near[i]) * (dynamic * dynamic + share * share)    // o preco informado ja traz a dinamica e o transito daquele instante
                val tariff  = Oracle.getTariff(calibration.market.level, route.distance, state.minutes[i], state.excess[i], company)
                means.getValue("p")[s][i]  = ln(tariff) + (calibration.mean + near[i] * (calibration.last - calibration.mean))
                sigmas.getValue("p")[s][i] = sqrt(max(spread, SIGMA_MIN * SIGMA_MIN))
                means.getValue("m")[s][i]  = ln(state.minutes[i])
                sigmas.getValue("m")[s][i] = sqrt(error * error + travel * travel)
            }
        }

        val out = LinkedHashMap<String, DoubleArray>()

        for (prefix in listOf("p", "m")) {
            val (lower, middle, upper) = QUANTILES.values.map { q -> DoubleArray(n) { i -> exp(getQuantile(q, wet[i], doubleArrayOf(means.getValue(prefix)[0][i], means.getValue(prefix)[1][i]), doubleArrayOf(sigmas.getValue(prefix)[0][i], sigmas.getValue(prefix)[1][i]))) } }
            out["${prefix}10"] = DoubleArray(n) { min(lower[it], middle[it] * (1 - MIN_BAND / 2)) }
            out["${prefix}50"] = middle
            out["${prefix}90"] = DoubleArray(n) { max(upper[it], middle[it] * (1 + MIN_BAND / 2)) }
        }

        return out
    }

    fun get(route: Route, ts: LongArray, rain: DoubleArray, probability: DoubleArray, company: String, calibration: Calibration) = getBands(route, ts, rain, probability, company, calibration).mapValues { (key, values) -> DoubleArray(values.size) { getRound(values[it], DIGITS.getValue(key.take(1))) } }
}
