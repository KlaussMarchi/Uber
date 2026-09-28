package com.klauss.tarifa

import java.io.File
import java.time.LocalDate
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import kotlin.math.abs


// O APP REPRODUZ O DESKTOP: CADA SECAO DO GOLDEN.JSON (GERADO PELO GOLDEN.PY A PARTIR DO CODIGO PYTHON) E CONFERIDA AQUI
class ParityTest {
    companion object {
        val golden = JSONObject(File("src/test/resources/golden.json").readText())

        @JvmStatic @BeforeClass
        fun setup() = Oracle.load(File("src/main/assets/markets.json").readText())

        fun getLongs(array: JSONArray) = LongArray(array.length()) { array.getLong(it) }
        fun getDoubles(array: JSONArray) = DoubleArray(array.length()) { array.optDouble(it) }
        fun getRoute(item: JSONObject) = Route(item.getLong("id"), "", "", item.getDouble("o_lat"), item.getDouble("o_lon"), 0.0, 0.0, item.getDouble("distance"), item.getDouble("duration"), item.getString("tz"), item.getString("city"), item.getString("uf"))
        fun getPlace(item: JSONObject) = Place(item.getString("label"), item.getDouble("lat"), item.getDouble("lon"), if (item.has("accuracy")) item.getDouble("accuracy") else null)
        fun getRides(array: JSONArray) = List(array.length()) { array.getJSONObject(it) }.map { Ride(it.getString("company"), it.getLong("ts"), it.getDouble("lat"), it.getDouble("lon"), it.getString("city"), it.getString("uf"), it.getDouble("level"), it.getDouble("expected"), it.getDouble("observed")) }
    }

    @Test
    fun holidays() {
        val years = golden.getJSONObject("holidays")

        for (year in years.keys()) {
            val expected = years.getJSONArray(year).let { days -> List(days.length()) { LocalDate.parse(days.getString(it)) } }
            assertEquals(expected, getHolidays(year.toInt()).sorted())
        }
    }

    @Test
    fun clock() {
        val cases = golden.getJSONArray("clock")

        for (i in 0 until cases.length()) {
            val case  = cases.getJSONObject(i)
            val clock = getClock(getLongs(case.getJSONArray("ts")), case.getString("tz"))
            assertTrue(getLongs(case.getJSONArray("weekday")).map(Long::toInt).toIntArray().contentEquals(clock.weekday))
            assertTrue(getDoubles(case.getJSONArray("hour")).contentEquals(clock.hour))
            assertTrue(getLongs(case.getJSONArray("day")).contentEquals(clock.day))
        }
    }

    // DURACAO E DIFERENCA DE TEMPO COM O MESMO TEXTO E O MESMO ARREDONDAMENTO DO DESKTOP (47 min, 2h05, +14 min)
    @Test
    fun durations() {
        val cases = golden.getJSONArray("durations")

        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            assertEquals(case.getString("duration"), getDuration(case.getDouble("minutes")))
            assertEquals(case.getString("delay"), getDelay(case.getDouble("minutes")))
        }
    }

    // FAIXA DE TEMPO DE VIAGEM COM O MESMO TEXTO DO DESKTOP (40 a 45 min, 55 min a 1h05)
    @Test
    fun spans() {
        val cases = golden.getJSONArray("spans")

        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            assertEquals(case.getString("span"), getSpan(case.getDouble("low"), case.getDouble("high")))
        }
    }

    // PONTO INICIAL DO MAPA DO BOTAO DE LOCALIZACAO (ESTIMATIVA, LUGAR USADO DENTRO DO ERRO, ULTIMO LUGAR OU REGIAO) IGUAL AO DO DESKTOP
    @Test
    fun starts() {
        val cases = golden.getJSONArray("starts")

        for (i in 0 until cases.length()) {
            val case   = cases.getJSONObject(i)
            val recent = case.getJSONArray("recent").let { items -> List(items.length()) { getPlace(items.getJSONObject(it)) } }
            val (place, zoom, mode) = Locator.getStart(case.optJSONObject("estimate")?.let(::getPlace), recent)
            assertEquals(listOf(case.getString("label"), case.getDouble("lat"), case.getDouble("lon"), case.getInt("zoom"), case.getString("mode")), listOf(place.label, place.lat, place.lon, zoom, mode))
        }
    }

    // ALERTAS A CADA 10% DE AFASTAMENTO SEM REPETIR NO MESMO PATAMAR, NA MESMA SEQUENCIA QUE O TEST.PY DO DESKTOP CONFERE
    @Test
    fun alerts() {
        val route = Route(-1, "", "", 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, "America/Sao_Paulo")
        Tracker.baselines[route.id to "uber"] = 100.0 to "10:00"
        Tracker.levels[route.id to "uber"] = 0
        assertEquals(listOf("good", "good", "bad", "bad"), listOf(95.0, 89.0, 85.0, 79.0, 88.0, 105.0, 111.0, 125.0).mapNotNull { Tracker.check(route, "uber", it) })
    }

    // MUNICIPIO PELA CHAVE SEM ACENTO NEM CAIXA, COMO O NOMINATIM E O IBGE ESCREVEM DIFERENTE
    @Test
    fun keys() {
        val cases = golden.getJSONArray("keys")

        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            assertEquals(case.getString("key"), Oracle.getKey(case.getString("city"), case.getString("uf")))
        }
    }

    // CONGESTIONAMENTO E DEMANDA DA SEMANA LOCAL IGUAIS BIT A BIT: A SOMA DOS PICOS E SEQUENCIAL NOS DOIS
    @Test
    fun profile() {
        val item    = golden.getJSONObject("profile")
        val weekday = getLongs(item.getJSONArray("weekday"))
        val hour    = getDoubles(item.getJSONArray("hour"))
        val traffic = getDoubles(item.getJSONArray("traffic"))
        val demand  = getDoubles(item.getJSONArray("demand"))

        for (i in weekday.indices) {
            val (jam, rush) = Oracle.getProfile(weekday[i].toInt(), hour[i])
            assertEquals(traffic[i], jam, 1e-15)
            assertEquals(demand[i], rush, 1e-15)
        }
    }

    // MERCADO NA ORIGEM: O NIVEL DO MUNICIPIO DA TABELA, DOS VIZINHOS DO MESMO ESTADO OU DO ESTADO, COM O MESMO NOME NA TELA
    @Test
    fun markets() {
        val cases = golden.getJSONArray("markets")

        for (i in 0 until cases.length()) {
            val case   = cases.getJSONObject(i)
            val market = Oracle.getMarket(case.getDouble("lat"), case.getDouble("lon"), case.getString("city"), case.getString("uf"))
            assertEquals(case.getString("market"), market.name)
            assertEquals(case.getDouble("level"), market.level, 1e-12)
            assertEquals(case.getDouble("pace"), market.pace, 1e-12)
            assertEquals(case.getDouble("sigma"), market.sigma, 1e-12)
        }
    }

    // ESTADO ESPERADO (DINAMICA, CONGESTIONAMENTO E MINUTOS) EM MILHARES DE INSTANTES, ROTAS, FUSOS E CHUVAS, E O TEMPO SEM TRANSITO
    @Test
    fun state() {
        val cases = golden.getJSONArray("state")

        for (i in 0 until cases.length()) {
            val case  = cases.getJSONObject(i)
            val route = getRoute(case.getJSONObject("route"))
            val state = Oracle.getState(route, getLongs(case.getJSONArray("ts")), getDoubles(case.getJSONArray("rain")))
            getDoubles(case.getJSONArray("excess")).forEachIndexed { k, value -> assertEquals(value, state.excess[k], 1e-12) }
            getDoubles(case.getJSONArray("traffic")).forEachIndexed { k, value -> assertEquals(value, state.traffic[k], 1e-12) }
            getDoubles(case.getJSONArray("minutes")).forEachIndexed { k, value -> assertEquals(value, state.minutes[k], 1e-9) }
            assertEquals(case.getDouble("free"), Oracle.getFree(route), 1e-9)
        }
    }

    // TARIFA, PARTE DOS MINUTOS E DINAMICA DE CADA APLICATIVO EM DISTANCIAS, TEMPOS, NIVEIS E DEMANDAS DE TODA ORDEM
    @Test
    fun tariff() {
        val cases = golden.getJSONArray("tariff")

        for (i in 0 until cases.length()) {
            val case    = cases.getJSONObject(i)
            val company = case.getString("company")
            assertEquals(case.getDouble("tariff"), Oracle.getTariff(case.getDouble("level"), case.getDouble("distance"), case.getDouble("minutes"), case.getDouble("excess"), company), 1e-9)
            assertEquals(case.getDouble("share"), Oracle.getShare(case.getDouble("distance"), case.getDouble("minutes")), 1e-12)
            assertEquals(case.getDouble("surge"), Oracle.getSurge(case.getDouble("excess"), company), 0.0)
        }
    }

    // NORMAL ACUMULADA E QUANTIS DA MISTURA DE CHUVA IGUAIS AOS DO DESKTOP
    @Test
    fun quantiles() {
        val normal = golden.getJSONObject("normal")
        getDoubles(normal.getJSONArray("z")).zip(getDoubles(normal.getJSONArray("cdf"))).forEach { (z, cdf) -> assertEquals(cdf, Model.getNormal(z), 1e-15) }

        val cases = golden.getJSONArray("quantiles")

        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            assertEquals(case.getDouble("value"), Model.getQuantile(case.getDouble("q"), case.getDouble("wet"), getDoubles(case.getJSONArray("means")), getDoubles(case.getJSONArray("sigmas"))), 1e-12)
        }
    }

    // CALIBRACAO PELOS PRECOS INFORMADOS: OS MESMOS PRECOS GUARDADOS, O MESMO NIVEL, A MESMA VARIANCIA E A MESMA DINAMICA DO MAIS RECENTE
    @Test
    fun calibration() {
        val cases = golden.getJSONArray("calibration")

        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            Oracle.update(getRides(case.getJSONArray("rides")))
            val found = Oracle.getCalibration(getRoute(case.getJSONObject("route")), case.getString("company"), case.getLong("now"))
            assertEquals(case.getInt("kept"), Oracle.FARES.size)
            assertEquals(case.getJSONObject("market").getString("market"), found.market.name)
            assertEquals(case.getDouble("mean"), found.mean, 1e-12)
            assertEquals(case.getDouble("var"), found.variance, 1e-12)
            assertEquals(case.getDouble("last"), found.last, 1e-12)
            assertEquals(case.getLong("ts"), found.ts)
            assertEquals(case.getDouble("weight"), found.weight, 1e-12)
            assertEquals(case.getInt("count"), found.count)
        }

        Oracle.update(emptyList())
    }

    // FAIXAS DO MODELO (P10 A M90) IGUAIS AO CENTAVO E AO CENTESIMO DE MINUTO EM TODAS AS ROTAS, HORARIOS, CHUVAS E CALIBRACOES, INCLUSIVE ANCORADA
    @Test
    fun model() {
        val cases = golden.getJSONArray("model")
        var worst = 0.0

        for (i in 0 until cases.length()) {
            val case  = cases.getJSONObject(i)
            val route = getRoute(case.getJSONObject("route"))
            val now   = case.getLong("now")
            Oracle.update(getRides(case.getJSONArray("rides")))
            val calibration = if (case.getString("label") == "ancora") Oracle.getAnchor(route, now) else Oracle.getCalibration(route, case.getString("company"), now)
            val out = Model.get(route, getLongs(case.getJSONArray("ts")), getDoubles(case.getJSONArray("rain")), getDoubles(case.getJSONArray("probability")), case.getString("company"), calibration)

            for ((key, values) in out) getDoubles(case.getJSONObject("out").getJSONArray(key)).forEachIndexed { k, value -> worst = maxOf(worst, abs(value - values[k])) }
        }

        Oracle.update(emptyList())
        println("maior diferenca nas faixas em ${cases.length()} casos: $worst")
        assertEquals(0.0, worst, 1e-9)
    }

    // SERIE COMPLETA DO ENGINE (GRADE, CHUVA E CHANCE INTERPOLADAS, PRECO, TEMPO E DINAMICA AGORA E FAIXAS) COM O MESMO CLIMA E OS MESMOS PRECOS INFORMADOS
    @Test
    fun engine() {
        val cases = golden.getJSONArray("engine")

        for (i in 0 until cases.length()) {
            val case    = cases.getJSONObject(i)
            val weather = case.getJSONObject("weather")
            val out     = case.getJSONObject("out")
            Oracle.update(getRides(case.getJSONArray("rides")))
            val series  = Engine.getSeries(getRoute(case.getJSONObject("route")), case.getLong("now"), Weather(getDoubles(weather.getJSONArray("ts")), getDoubles(weather.getJSONArray("rain")), getDoubles(weather.getJSONArray("probability"))), case.getString("company"))!!

            assertTrue(getLongs(out.getJSONArray("ts")).contentEquals(series.ts))
            assertTrue(getDoubles(out.getJSONArray("rain")).contentEquals(series.rain))
            assertTrue(getDoubles(out.getJSONArray("probability")).contentEquals(series.probability))
            assertEquals(out.getJSONArray("price").getDouble(0), series.price, 0.0)
            assertEquals(out.getJSONArray("minutes").getDouble(0), series.minutes, 0.0)
            assertEquals(out.getJSONArray("surge").getDouble(0), series.surge, 1e-12)
            series.bands.forEach { (key, values) -> assertTrue(key, getDoubles(out.getJSONArray(key)).contentEquals(values)) }
        }

        Oracle.update(emptyList())
    }
}
