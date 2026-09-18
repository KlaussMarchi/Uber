package com.klauss.tarifa

import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt


// TARIFA DE UM APLICATIVO: BANDEIRADA, VALOR POR KM E POR MINUTO, TAXA FIXA, PISO DA CORRIDA, SENSIBILIDADE A DINAMICA E TETO DELA
data class Fare(val base: Double, val km: Double, val minute: Double, val fee: Double, val floor: Double, val surge: Double, val cap: Double)

// PRECO REAL LIDO NO APLICATIVO PELO USUARIO, COM O ESTADO DO MERCADO DAQUELE INSTANTE
class Ride(val company: String, val distance: Double, val minutes: Double, val surge: Double, val observed: Double)

// ESTADO DO MERCADO NUM INSTANTE, O MESMO PARA OS DOIS APLICATIVOS
class State(val excess: DoubleArray, val noise: DoubleArray, val minutes: DoubleArray)

// MERCADO SIMULADO QUE DEFINE O PRECO "REAL": MESMA FORMULA, MESMAS CONSTANTES E MESMO SORTEIO DO ORACLE/INDEX.PY DO DESKTOP
object Oracle {
    // tabela publicada da UberX na capital do Rio em 2026: bandeirada 2,30, R$ 1,55/km, R$ 0,32/min, minima 10,50 e reserva 0,75; a 99 nao publica tabela fixa
    val TARIFFS = linkedMapOf(
        "uber" to Fare(2.50, 1.2076, 0.2200, 1.00, 7.50, 1.00, 2.50),    // calibrada no desktop para a mediana da rota de referencia em dia util, 9-16 h e sem chuva, dar R$ 54,85
        "99"   to Fare(2.10, 1.1147, 0.2031, 0.00, 6.50, 0.78, 2.20),    // cerca de 10% abaixo da uber fora do pico e mais ainda na dinamica, como o mercado
    )

    const val RIDGE  = 6.0      // observacoes equivalentes que a forma da tabela publicada vale no ajuste; o nivel ja se move com a primeira
    const val SAMPLE = 20       // precos reais mais recentes de cada aplicativo que entram no ajuste
    const val DIGITS = 6        // casas da tarifa ajustada; arredondar aqui faz o celular e o computador chegarem na mesma tabela apesar do solver diferente
    val LIMITS = 0.5..2.0       // correcao maxima de cada termo, para um preco digitado errado nao destruir a tarifa

    const val URBAN          = 0.18     // atraso relativo no pico fora do corredor
    const val CORRIDOR       = 0.60     // atraso relativo no pico na Amaral Peixoto
    const val INCIDENT_DELAY = 0.80
    const val RAIN_DELAY     = 0.25
    const val RAIN_MM        = 4.0      // chuva (mm/h) que produz 63% do efeito maximo
    const val DEMAND_SURGE   = 0.20
    const val RAIN_SURGE     = 0.32
    const val INCIDENT_SURGE = 0.25
    const val INCIDENTS      = 0.7      // acidentes por dia no corredor
    const val INCIDENT_TAU   = 2700.0   // s ate o congestionamento de um acidente cair a 37%
    const val NOISE          = 0.025    // desvio log do preco entre slots (oferta de motoristas)
    const val DAY_NOISE      = 0.015    // desvio log do nivel de cada dia local
    const val TIME_NOISE     = 0.04     // desvio log do tempo de viagem entre slots (semaforos, motorista, fila)
    const val DAY_TIME_NOISE = 0.02     // desvio log do transito de cada dia local (obra, ferias escolares, evento)
    const val SLOTS          = 150      // slots de ruido por dia; 25 h cobrem o dia de fim de horario de verao
    const val SEED           = 7L

    // dia da semana (0 = segunda, feriado entra como 6), hora do pico, amplitude da demanda, largura (h); mesma ordem do desktop
    val PEAKS = (0..4).map { doubleArrayOf(it.toDouble(), 7.00, 0.70, 0.9) } + (0..4).map { doubleArrayOf(it.toDouble(), 12.25, 0.15, 0.7) } + (0..3).map { doubleArrayOf(it.toDouble(), 17.75, 0.85, 1.1) } + listOf(
        doubleArrayOf(4.0, 18.00, 1.00, 1.3),
        doubleArrayOf(4.0, 23.00, 0.30, 1.5),
        doubleArrayOf(5.0, 11.00, 0.35, 2.0),
        doubleArrayOf(5.0, 21.00, 0.30, 2.5),
        doubleArrayOf(6.0, 17.00, 0.55, 2.0),
    )

    @Volatile var FARES = TARIFFS    // tabela em uso; cada preco real informado pelo usuario desloca ela

    fun getFare(company: String) = FARES.getValue(company)

    // TARIFA DO APLICATIVO; SEM DINAMICA E NA DURACAO LIVRE E A BASE QUE O MODELO USA PARA NORMALIZAR O PRECO
    fun getTariff(distance: Double, duration: Double, surge: Double = 1.0, company: String = "uber"): Double {
        val fare = getFare(company)
        return fare.fee + max(fare.floor, fare.base + fare.km * distance + fare.minute * duration) * surge
    }

    // AJUSTE DA TARIFA AOS PRECOS REAIS INFORMADOS: O NIVEL SEGUE A PRIMEIRA OBSERVACAO E A FORMA SO SE MOVE QUANDO VARIAS ROTAS DISCORDAM DA TABELA PUBLICADA
    fun update(rows: List<Ride>) {
        FARES = linkedMapOf(*TARIFFS.map { (company, fare) -> company to getFitted(fare, rows.filter { it.company == company && it.observed > fare.fee }.takeLast(SAMPLE)) }.toTypedArray())
    }

    fun getFitted(fare: Fare, found: List<Ride>): Fare {
        val terms = found.map { doubleArrayOf(fare.base, fare.km * it.distance, fare.minute * it.minutes) }
        val above = terms.indices.filter { terms[it].sum() > fare.floor }    // abaixo da tarifa minima o preco nao depende dos coeficientes, so do piso
        val below = terms.indices.filter { terms[it].sum() <= fare.floor }
        val floor = if (below.isEmpty()) fare.floor else getRound(fare.floor * getBounded(getMedian(below.map { (found[it].observed - fare.fee) / max(found[it].surge, 1e-9) }) / fare.floor), DIGITS)

        if (above.isEmpty()) return fare.copy(floor = floor)

        val rows   = above.map { i -> DoubleArray(3) { terms[i][it] * found[i].surge } }
        val target = above.map { found[it].observed - fare.fee }
        val whole  = rows.map { it.sum() }
        val level  = whole.indices.sumOf { whole[it] * target[it] } / max(whole.sumOf { it * it }, 1e-9)
        val scale  = DoubleArray(3) { k -> sqrt(rows.sumOf { it[k] * it[k] } / rows.size) + 1e-9 }
        val matrix = Array(3) { r -> DoubleArray(3) { c -> rows.sumOf { it[r] / scale[r] * (it[c] / scale[c]) } + if (r == c) RIDGE else 0.0 } }
        val side   = DoubleArray(3) { r -> rows.indices.sumOf { rows[it][r] / scale[r] * target[it] } + RIDGE * level * scale[r] }
        val shape  = getSolved(matrix, side)
        val term   = { k: Int, value: Double -> getRound(value * getBounded(shape[k] / scale[k]), DIGITS) }
        return fare.copy(base = term(0, fare.base), km = term(1, fare.km), minute = term(2, fare.minute), floor = floor)
    }

    // CORRECAO ACEITA PARA UM TERMO DA TARIFA; UM PRECO DIGITADO ERRADO NAO TIRA A TABELA DA FAIXA PLAUSIVEL
    fun getBounded(value: Double) = if (value.isFinite()) value.coerceIn(LIMITS.start, LIMITS.endInclusive) else 1.0

    // MEDIANA COMO A DO NUMPY: COM NUMERO PAR DE VALORES, A MEDIA DOS DOIS DO MEIO
    fun getMedian(values: List<Double>): Double {
        val sorted = values.sorted()
        val half   = sorted.size / 2
        return if (sorted.size % 2 == 1) sorted[half] else (sorted[half - 1] + sorted[half]) / 2
    }

    // SISTEMA 3X3 PELA REGRA DE CRAMER, A MESMA CONTA EM QUALQUER LINGUAGEM; A CRISTA GARANTE QUE A MATRIZ E INVERSIVEL
    fun getSolved(matrix: Array<DoubleArray>, side: DoubleArray) = DoubleArray(3) { k -> getDeterminant(Array(3) { r -> DoubleArray(3) { c -> if (c == k) side[r] else matrix[r][c] } }) / getDeterminant(matrix) }

    fun getDeterminant(m: Array<DoubleArray>) = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])

    // DEMANDA RELATIVA NA SEMANA LOCAL: PICOS GAUSSIANOS COM DISTANCIA CIRCULAR DE 168 H, QUE ATRAVESSA MEIA-NOITE E DOMINGO
    fun getDemand(weekday: Int, hour: Double) = PEAKS.sumOf { peak ->
        val z = ((weekday * 24 + hour - peak[0] * 24 - peak[1] + 84).mod(168.0) - 84) / peak[3]
        peak[2] * exp(-0.5 * (z * z))
    }

    // SORTEIOS COM SEMENTE POR DIA LOCAL: ACIDENTES NO CORREDOR (COMUNS AS ROTAS), RUIDO DE OFERTA E DE TEMPO DE VIAGEM DE CADA ROTA
    fun getRandom(ts: LongArray, routeId: Long, days: LongArray, tz: String): Array<DoubleArray> {
        val incident = DoubleArray(ts.size)
        val noise    = DoubleArray(ts.size)
        val jitter   = DoubleArray(ts.size)

        for (day in days.min() - 1..days.max()) {
            val midnight  = getMidnight(day, tz).toDouble()
            var rng       = Rng(SEED, day)
            val starts    = DoubleArray(rng.getPoisson(INCIDENTS)) { midnight + 86400.0 * rng.getDouble() }
            val amplitude = DoubleArray(starts.size) { 0.3 + (1.0 - 0.3) * rng.getDouble() }

            for (i in ts.indices) {
                var sum = 0.0

                for (k in starts.indices) {
                    val age = ts[i] - starts[k]
                    sum += amplitude[k] * exp(-max(age, 0.0) / INCIDENT_TAU) * (if (age >= 0) 1.0 else 0.0)
                }

                incident[i] += sum
            }

            rng = Rng(SEED, routeId, day)
            val dayNoise   = DAY_NOISE * rng.getNormal()
            val slotNoise  = DoubleArray(SLOTS) { NOISE * rng.getNormal() }
            val dayJitter  = DAY_TIME_NOISE * rng.getNormal()
            val slotJitter = DoubleArray(SLOTS) { TIME_NOISE * rng.getNormal() }

            for (i in ts.indices) {
                if (days[i] != day) continue
                val slot = Math.floorDiv((ts[i] - midnight).toLong(), STEP).toInt()
                noise[i]  = dayNoise + slotNoise[slot]
                jitter[i] = dayJitter + slotJitter[slot]
            }
        }

        return arrayOf(incident, noise, jitter)
    }

    // ESTADO DO MERCADO NO INSTANTE, O MESMO PARA OS DOIS APLICATIVOS: EXCESSO DE DEMANDA, RUIDO DA OFERTA E MINUTOS DE VIAGEM
    fun getState(route: Route, ts: LongArray, rain: DoubleArray): State {
        val clock = getClock(ts, route.tz)
        val (incident, noise, jitter) = getRandom(ts, route.id, clock.day, route.tz)
        val excess  = DoubleArray(ts.size)
        val minutes = DoubleArray(ts.size)

        for (i in ts.indices) {
            val wet    = 1 - exp(-rain[i] / RAIN_MM)
            val demand = getDemand(clock.weekday[i], clock.hour[i])
            val delay  = (URBAN + CORRIDOR * route.corridor) * demand + INCIDENT_DELAY * route.corridor * incident[i] + RAIN_DELAY * wet
            excess[i]  = DEMAND_SURGE * demand + RAIN_SURGE * wet + INCIDENT_SURGE * route.corridor * incident[i]
            minutes[i] = getRound(route.duration * (1 + delay) * exp(jitter[i]), 1)
        }

        return State(excess, noise, minutes)
    }

    // MULTIPLICADOR EFETIVO DO APLICATIVO: A DINAMICA DELE SOBRE O EXCESSO DE DEMANDA, LIMITADA PELO TETO, MAIS O RUIDO DA OFERTA, QUE E DO MERCADO
    fun getSurge(excess: Double, noise: Double, company: String): Double {
        val fare = getFare(company)
        return min(1 + fare.surge * excess, fare.cap) * exp(noise)
    }

    // PRECO QUE O APLICATIVO MOSTRA A PARTIR DO ESTADO GUARDADO
    fun getPrice(distance: Double, minutes: Double, excess: Double, noise: Double, company: String) = getRound(getTariff(distance, minutes, getSurge(excess, noise, company), company), 2)

    // PRECO E TEMPO DE VIAGEM QUE O APLICATIVO MOSTRA NO INSTANTE: O MESMO ATRASO DE TRANSITO ALONGA A VIAGEM E ENTRA NA TARIFA
    fun getMarket(route: Route, ts: LongArray, rain: DoubleArray, company: String = "uber"): Pair<DoubleArray, DoubleArray> {
        val state = getState(route, ts, rain)
        return DoubleArray(ts.size) { getPrice(route.distance, state.minutes[it], state.excess[it], state.noise[it], company) } to state.minutes
    }
}
