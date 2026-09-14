package com.klauss.tarifa

import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min


// MERCADO SIMULADO QUE DEFINE O PRECO "REAL": MESMA FORMULA, MESMAS CONSTANTES E MESMO SORTEIO DO ORACLE/INDEX.PY DO DESKTOP
object Oracle {
    const val BASE     = 2.50
    const val PER_KM   = 1.2076    // calibrado no desktop para a mediana da rota de referencia em dia util, 9-16 h e sem chuva, dar R$ 54,85
    const val PER_MIN  = 0.22
    const val FEE      = 1.00
    const val MIN_FARE = 7.50

    const val URBAN          = 0.18     // atraso relativo no pico fora do corredor
    const val CORRIDOR       = 0.60     // atraso relativo no pico na Amaral Peixoto
    const val INCIDENT_DELAY = 0.80
    const val RAIN_DELAY     = 0.25
    const val RAIN_MM        = 4.0      // chuva (mm/h) que produz 63% do efeito maximo
    const val DEMAND_SURGE   = 0.20
    const val RAIN_SURGE     = 0.32
    const val INCIDENT_SURGE = 0.25
    const val SURGE_MAX      = 2.5
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

    // TARIFA DA PLATAFORMA; SEM DINAMICA E NA DURACAO LIVRE E A BASE QUE O MODELO USA PARA NORMALIZAR O PRECO
    fun getTariff(distance: Double, duration: Double, surge: Double = 1.0) = FEE + max(MIN_FARE, BASE + PER_KM * distance + PER_MIN * duration) * surge

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

    // PRECO E TEMPO DE VIAGEM QUE O MERCADO MOSTRA NO INSTANTE: O MESMO ATRASO DE TRANSITO ALONGA A VIAGEM E ENTRA NA TARIFA
    fun getMarket(route: Route, ts: LongArray, rain: DoubleArray): Pair<DoubleArray, DoubleArray> {
        val clock = getClock(ts, route.tz)
        val (incident, noise, jitter) = getRandom(ts, route.id, clock.day, route.tz)
        val price   = DoubleArray(ts.size)
        val minutes = DoubleArray(ts.size)

        for (i in ts.indices) {
            val wet    = 1 - exp(-rain[i] / RAIN_MM)
            val demand = getDemand(clock.weekday[i], clock.hour[i])
            val delay  = (URBAN + CORRIDOR * route.corridor) * demand + INCIDENT_DELAY * route.corridor * incident[i] + RAIN_DELAY * wet
            val surge  = min(1 + DEMAND_SURGE * demand + RAIN_SURGE * wet + INCIDENT_SURGE * route.corridor * incident[i], SURGE_MAX)
            val trip   = route.duration * (1 + delay) * exp(jitter[i])
            price[i]   = getRound(getTariff(route.distance, trip, surge * exp(noise[i])), 2)
            minutes[i] = getRound(trip, 1)
        }

        return price to minutes
    }
}
