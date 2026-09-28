package com.klauss.tarifa

import java.text.Normalizer
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.min
import kotlin.math.sqrt


// APLICATIVO: RAZAO SOBRE A UBERX, SENSIBILIDADE A DINAMICA E TETO DELA
data class Tariff(val ratio: Double, val surge: Double, val cap: Double)

// PRECO REAL INFORMADO PELO USUARIO, COM O MUNICIPIO DA ORIGEM, O NIVEL DA REGIAO E O PRECO CENTRAL QUE O APP MOSTRAVA SEM CALIBRACAO NAQUELE INSTANTE
class Ride(val company: String, val ts: Long, val lat: Double, val lon: Double, val city: String, val uf: String, val level: Double, val expected: Double, val observed: Double)

// ESTADO ESPERADO NOS INSTANTES PEDIDOS: EXCESSO DE DEMANDA, CONGESTIONAMENTO RELATIVO E MINUTOS DE VIAGEM
class State(val excess: DoubleArray, val traffic: DoubleArray, val minutes: DoubleArray)

// NIVEL DE PRECO (LOG), RITMO (LOG) E INCERTEZA NA ORIGEM, COM O NOME QUE A TELA MOSTRA
class Market(val name: String, val level: Double, val pace: Double, val sigma: Double)

// NIVEL APRENDIDO DOS PRECOS INFORMADOS E A DINAMICA DO MAIS RECENTE, QUE SE DISSIPA
class Calibration(val mean: Double, val variance: Double, val last: Double, val ts: Long, val weight: Double, val count: Int, val market: Market)

// MUNICIPIO DA TABELA: SEDE, NIVEL E RITMO AJUSTADOS AS MEDIAS REAIS DA UBERX
class City(val name: String, val uf: String, val lat: Double, val lon: Double, val level: Double, val pace: Double)

// TABELA DO MARKET.PY DO DESKTOP: TARIFA SEM DINAMICA, INCLINACAO DO RITMO, INCERTEZAS E O NIVEL DE CADA ESTADO E MUNICIPIO
class Table(data: JSONObject) {
    val updated  = data.getString("updated")
    val routes   = data.getInt("routes")
    val tariff   = data.getJSONObject("tariff")
    val base     = tariff.getDouble("base")
    val km       = tariff.getDouble("km")
    val minute   = tariff.getDouble("minute")
    val long     = tariff.getDouble("long")
    val floor    = tariff.getDouble("floor")
    val paceKm   = data.getJSONObject("pace").getDouble("km")
    val speed    = data.getJSONObject("pace").getDouble("speed")
    val traffic  = data.getDouble("traffic")
    val week     = data.getJSONArray("week").let { values -> DoubleArray(values.length()) { values.getDouble(it) } }    // congestionamento real de cada hora da semana
    val sigma    = data.getJSONObject("sigma")
    val national = getPair(data.getJSONArray("national"))
    val states   = data.getJSONObject("states").let { item -> item.keys().asSequence().associateWith { getPair(item.getJSONArray(it)) } }
    val cities   = data.getJSONArray("cities").let { items -> List(items.length()) { items.getJSONArray(it).let { c -> City(c.getString(0), c.getString(1), c.getDouble(2), c.getDouble(3), c.getDouble(4), c.getDouble(5)) } } }
    val keys     = cities.withIndex().associate { (index, city) -> Oracle.getKey(city.name, city.uf) to index }

    fun getPair(array: JSONArray) = doubleArrayOf(array.getDouble(0), array.getDouble(1))
}

// MERCADO ESPERADO QUE DEFINE O PRECO: MESMAS FORMULAS, CONSTANTES E TABELA DO ORACLE/INDEX.PY DO DESKTOP
object Oracle {
    // a 99 nao publica precos e fica na razao tipica das comparacoes publicas
    val TARIFFS = linkedMapOf(
        "uber" to Tariff(1.00, 1.00, 2.50),
        "99"   to Tariff(0.90, 1.00, 2.50),
    )

    const val DEMAND_SURGE = 0.35     // dinamica no pico de demanda mais forte da semana
    const val RAIN_SURGE   = 0.40     // dinamica somada pela chuva forte
    const val RAIN_DELAY   = 0.25     // tempo de viagem a mais com chuva forte
    const val RAIN_MM      = 4.0      // chuva (mm/h) que produz 63% do efeito maximo
    const val FREE         = 0.25     // parte do atraso medio que continua de madrugada (semaforo, conversao, via lenta)
    const val BANDWIDTH    = 20.0     // km em que o nivel de uma cidade vizinha ainda pesa
    const val PRIOR        = 0.05     // peso do estado frente as cidades proximas; longe de todas vale o estado
    const val KM_REF       = 20.0
    const val LONG         = 40.0     // km a partir dos quais a uber cobra mais por km: a viagem intermunicipal volta vazia
    const val SPEED_REF    = 50.0     // km/h
    const val SAMPLE       = 20       // precos informados mais recentes de cada aplicativo
    const val RADIUS       = 50.0     // km em que um preco informado ainda diz algo sobre outra origem
    const val TAU          = 2700.0   // s ate a dinamica vista num preco informado cair a 37%
    const val HOLD         = 120L     // s em que o preco mostrado pelo app ainda vale inteiro
    const val DYNAMIC      = 0.10     // desvio log do preco real em torno do esperado fora do pico; separa o nivel da dinamica
    val PACE   = 1.0..3.5             // ritmo real sobre o osrm; fora disso a regressao extrapola
    val LIMITS = 0.5..2.0             // preco informado fora disso em relacao ao esperado e erro de digitacao, nao mercado

    // dia (0 = segunda, feriado vale 6), hora do centro, largura (h), demanda; picos da procura por corrida que movem a dinamica, na mesma ordem do desktop
    val PEAKS = (0..4).map { doubleArrayOf(it.toDouble(), 13.00, 3.5, 0.10) } + (0..4).map { doubleArrayOf(it.toDouble(), 7.75, 1.0, 0.55) } + (0..3).map { doubleArrayOf(it.toDouble(), 18.00, 1.5, 0.70) } + listOf(
        doubleArrayOf(4.0, 18.25, 1.7, 0.85),
        doubleArrayOf(4.0, 23.50, 1.5, 0.45),
        doubleArrayOf(5.0, 12.00, 3.0, 0.15),
        doubleArrayOf(5.0, 22.00, 2.5, 0.50),
        doubleArrayOf(6.0, 1.50, 1.5, 0.35),
        doubleArrayOf(6.0, 12.00, 3.0, 0.10),
        doubleArrayOf(6.0, 18.00, 2.0, 0.35),
    )

    @Volatile var MARKET: Table? = null           // tabela ajustada pelo market.py do desktop
    @Volatile var FARES = emptyList<Ride>()        // precos reais informados, os mais recentes de cada aplicativo

    fun load(text: String) {
        MARKET = Table(JSONObject(text))
    }

    fun ready() = MARKET != null

    // CHAVE DO MUNICIPIO SEM ACENTO NEM CAIXA, COMO O NOMINATIM E O IBGE ESCREVEM DIFERENTE
    fun getKey(city: String, uf: String) = Normalizer.normalize("$city/$uf", Normalizer.Form.NFD).replace(Regex("\\p{Mn}+"), "").lowercase()

    // PRECOS REAIS INFORMADOS: OS MAIS RECENTES DE CADA APLICATIVO FICAM EM MEMORIA PARA A CALIBRACAO DE CADA CONSULTA
    fun update(rows: List<Ride>) {
        val sorted = rows.sortedBy { it.ts }
        val recent = sorted.groupBy { it.company }.values.flatMap { it.takeLast(SAMPLE) }.toSet()
        FARES = sorted.filter { it in recent }
    }

    // CONGESTIONAMENTO REAL DA HORA LOCAL (INTERPOLADO ENTRE OS CENTROS DAS HORAS, CIRCULAR NA SEMANA) E DEMANDA POR PICOS GAUSSIANOS COM DISTANCIA CIRCULAR DE 168 H
    fun getProfile(weekday: Int, hour: Double): Pair<Double, Double> {
        val table  = MARKET!!.week
        val week   = (weekday * 24 + hour - 0.5).mod(168.0)    // o valor de cada hora vale no meio dela
        val index  = floor(week).toInt()
        val share  = week - index
        var demand = 0.0

        for (peak in PEAKS) {
            val delta = (weekday * 24 + hour - peak[0] * 24 - peak[1] + 84).mod(168.0) - 84
            demand += exp(-0.5 * ((delta / peak[2]) * (delta / peak[2]))) * peak[3]
        }

        return table[index] * (1 - share) + table[(index + 1) % 168] * share to demand
    }

    // NIVEL DE PRECO, RITMO E INCERTEZA NA ORIGEM: O DO MUNICIPIO QUANDO A UBER PUBLICA TRECHOS DELE; SENAO A MEDIA DOS VIZINHOS DO MESMO ESTADO, PUXADA PARA O ESTADO QUANDO NAO HA NENHUM PERTO
    fun getMarket(lat: Double, lon: Double, city: String = "", uf: String = ""): Market {
        val table = MARKET!!
        val cityS = table.sigma.getDouble("city")
        val index = table.keys[getKey(city, uf)]

        if (index != null) return table.cities[index].let { Market("${it.name}/${it.uf}", it.level, it.pace, cityS) }

        val distance = DoubleArray(table.cities.size) { getDistance(lat, lon, table.cities[it].lat, table.cities[it].lon) }
        val state    = uf.ifEmpty { table.cities[distance.indices.minBy { distance[it] }].uf }
        val same     = table.cities.map { it.uf == state }
        val prior    = table.states[state] ?: table.national
        val weight   = DoubleArray(distance.size) { if (same[it]) exp(-((distance[it] / BANDWIDTH) * (distance[it] / BANDWIDTH))) else 0.0 }
        val total    = weight.sum() + PRIOR
        val near     = distance.indices.filter { same[it] }.minByOrNull { distance[it] }
        val stateS   = table.sigma.getDouble("state")
        val name     = if (near != null && distance[near] <= 2 * BANDWIDTH) "${table.cities[near].name}/$state e arredores" else if (state in table.states) "$state (média do estado)" else "média nacional"
        val level    = (weight.indices.sumOf { weight[it] * table.cities[it].level } + PRIOR * prior[0]) / total
        val pace     = (weight.indices.sumOf { weight[it] * table.cities[it].pace } + PRIOR * prior[1]) / total
        return Market(name, level, pace, sqrt(cityS * cityS + (stateS * stateS - cityS * cityS) * PRIOR / total))
    }

    fun getOrigin(route: Route) = getMarket(route.oLat, route.oLon, route.city, route.uf)

    // RITMO MEDIO DO MES SOBRE O TEMPO DO OSRM: O OSRM ERRA MAIS NA CIDADE (CURTA E LENTA) E NA RODOVIA RAPIDA, E CADA REGIAO TEM O SEU TRANSITO
    fun getPace(route: Route, pace: Double): Double {
        val table = MARKET!!
        val speed = route.distance / route.duration * 60
        return exp(pace + table.paceKm * ln(route.distance / KM_REF) + table.speed * ln(speed / SPEED_REF)).coerceIn(PACE.start, PACE.endInclusive)
    }

    // ESTADO ESPERADO NO INSTANTE, O MESMO PARA OS DOIS APLICATIVOS: EXCESSO DE DEMANDA, CONGESTIONAMENTO RELATIVO E MINUTOS DE VIAGEM
    fun getState(route: Route, ts: LongArray, rain: DoubleArray): State {
        val table   = MARKET!!
        val clock   = getClock(ts, route.tz)
        val pace    = getPace(route, getOrigin(route).pace)
        val free    = 1 + (pace - 1) * FREE
        val excess  = DoubleArray(ts.size)
        val traffic = DoubleArray(ts.size)
        val minutes = DoubleArray(ts.size)

        for (i in ts.indices) {
            val (jam, rush) = getProfile(clock.weekday[i], clock.hour[i])
            val wet    = 1 - exp(-rain[i] / RAIN_MM)
            excess[i]  = DEMAND_SURGE * rush + RAIN_SURGE * wet
            traffic[i] = jam / table.traffic
            minutes[i] = route.duration * (free + (pace - free) * jam / table.traffic) * (1 + RAIN_DELAY * wet)
        }

        return State(excess, traffic, minutes)
    }

    // TEMPO DE VIAGEM DE MADRUGADA, SEM TRANSITO: A REFERENCIA DO RESUMO E DO GRAFICO
    fun getFree(route: Route): Double {
        val pace = getPace(route, getOrigin(route).pace)
        return route.duration * (1 + (pace - 1) * FREE)
    }

    // MULTIPLICADOR DA DINAMICA DO APLICATIVO SOBRE O EXCESSO DE DEMANDA, LIMITADO PELO TETO
    fun getSurge(excess: Double, company: String): Double {
        val fare = TARIFFS.getValue(company)
        return min(1 + fare.surge * excess, fare.cap)
    }

    // PRECO SEM CALIBRACAO: TARIFA SEM DINAMICA NO NIVEL DA CIDADE DE ORIGEM, COM PISO DA CORRIDA CURTA, VEZES A DINAMICA
    fun getTariff(level: Double, distance: Double, minutes: Double, excess: Double, company: String): Double {
        val table = MARKET!!
        val base  = maxOf(table.floor, table.base + table.km * distance + table.minute * minutes + table.long * maxOf(distance - LONG, 0.0))
        return exp(level) * TARIFFS.getValue(company).ratio * base * getSurge(excess, company)
    }

    // PARTE DO PRECO QUE VEM DOS MINUTOS; E O QUANTO O ERRO DO TEMPO DE VIAGEM PESA NO PRECO
    fun getShare(distance: Double, minutes: Double): Double {
        val table = MARKET!!
        val time  = table.minute * minutes
        val base  = table.base + table.km * distance + time + table.long * maxOf(distance - LONG, 0.0)
        return if (base > table.floor) time / base else 0.0
    }

    // CALIBRACAO PELOS PRECOS INFORMADOS: NIVEL BAYESIANO (PRIORI DA REGIAO, PESO PELA DISTANCIA DA ORIGEM) E A DINAMICA DO PRECO MAIS RECENTE, QUE SE DISSIPA COM O TEMPO
    fun getCalibration(route: Route, company: String, now: Long): Calibration {
        val market = getOrigin(route)
        val rows   = FARES.filter { it.company == company && it.ts <= now }

        if (rows.isEmpty()) return Calibration(0.0, market.sigma * market.sigma, 0.0, now, 0.0, 0, market)

        val y      = rows.map { ln((it.observed / (it.expected * exp(getMarket(it.lat, it.lon, it.city, it.uf).level - it.level))).coerceIn(LIMITS.start, LIMITS.endInclusive)) }
        val weight = rows.map { exp(-getDistance(route.oLat, route.oLon, it.lat, it.lon) / RADIUS) }
        val last   = rows.size - 1
        val old    = List(last) { weight[it] / (DYNAMIC * DYNAMIC) }
        val v0     = 1 / (1 / (market.sigma * market.sigma) + old.sum())
        val m0     = v0 * old.indices.sumOf { old[it] * y[it] }
        val noise  = DYNAMIC * DYNAMIC / weight[last]
        return Calibration(m0 + v0 / (v0 + noise) * (y[last] - m0), v0 * noise / (v0 + noise), y[last], rows[last].ts, weight[last], weight.count { it > 0.5 }, market)
    }

    // CALIBRACAO NEUTRA QUE ANCORA EXATAMENTE NO INSTANTE: E COM ELA QUE O PRECO CENTRAL SEM CALIBRACAO E GUARDADO JUNTO DO PRECO INFORMADO
    fun getAnchor(route: Route, now: Long) = Calibration(0.0, 0.0, 0.0, now, 1.0, 0, getOrigin(route))
}
