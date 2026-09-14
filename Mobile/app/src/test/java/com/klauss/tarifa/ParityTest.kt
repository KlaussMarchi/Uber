package com.klauss.tarifa

import java.io.File
import java.time.LocalDate
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs


// O APP REPRODUZ O DESKTOP: CADA SECAO DO GOLDEN.JSON (GERADO PELO GOLDEN.PY A PARTIR DO CODIGO PYTHON) E CONFERIDA AQUI
class ParityTest {
    companion object {
        val golden = JSONObject(File("src/test/resources/golden.json").readText())

        fun getLongs(array: JSONArray) = LongArray(array.length()) { array.getLong(it) }
        fun getDoubles(array: JSONArray) = DoubleArray(array.length()) { array.getDouble(it) }
        fun getRoute(item: JSONObject) = Route(item.getLong("id"), "", "", 0.0, 0.0, 0.0, 0.0, item.getDouble("distance"), item.getDouble("duration"), item.getDouble("corridor"), item.getString("tz"))
        fun getPlace(item: JSONObject) = Place(item.getString("label"), item.getDouble("lat"), item.getDouble("lon"), if (item.has("accuracy")) item.getDouble("accuracy") else null)
    }

    @Test
    fun rng() {
        val cases = golden.getJSONArray("rng")

        for (i in 0 until cases.length()) {
            val case    = cases.getJSONObject(i)
            val entropy = getLongs(case.getJSONArray("entropy"))
            val raw     = Rng(*entropy).let { rng -> List(8) { rng.next() } }
            assertEquals(List(8) { case.getJSONArray("raw").getString(it).toULong() }, raw)
            Rng(*entropy).let { rng -> getDoubles(case.getJSONArray("normal")).forEach { assertEquals(it, rng.getNormal(), 0.0) } }
            Rng(*entropy).let { rng -> getLongs(case.getJSONArray("poisson")).forEach { assertEquals(it, rng.getPoisson(0.7).toLong()) } }
            Rng(*entropy).let { rng -> getDoubles(case.getJSONArray("double")).forEach { assertEquals(it, rng.getDouble(), 0.0) } }
        }
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
        val route = Route(-1, "", "", 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, "America/Sao_Paulo")
        Tracker.baselines[route.id] = 100.0 to "10:00"
        Tracker.levels[route.id] = 0
        assertEquals(listOf("good", "good", "bad", "bad"), listOf(95.0, 89.0, 85.0, 79.0, 88.0, 105.0, 111.0, 125.0).mapNotNull { Tracker.check(route, it) })
    }

    // SORTEIOS DO ORACULO: RUIDO E JITTER EXATOS; O ACIDENTE PODE DIFERIR NA ULTIMA CASA PELA ORDEM DA SOMA DO NUMPY
    @Test
    fun random() {
        val cases = golden.getJSONArray("random")

        for (i in 0 until cases.length()) {
            val case  = cases.getJSONObject(i)
            val route = getRoute(case.getJSONObject("route"))
            val ts    = getLongs(case.getJSONArray("ts"))
            val (incident, noise, jitter) = Oracle.getRandom(ts, route.id, getClock(ts, route.tz).day, route.tz)
            assertTrue(getDoubles(case.getJSONArray("noise")).contentEquals(noise))
            assertTrue(getDoubles(case.getJSONArray("jitter")).contentEquals(jitter))
            getDoubles(case.getJSONArray("incident")).forEachIndexed { k, value -> assertEquals(value, incident[k], 1e-12) }
        }
    }

    // SOMA DAS ARVORES DE CADA BOOSTER IGUAL AO PREDICT DO LIGHTGBM E QUANTIS REARRANJADOS NA CHUVA IGUAIS AO GETQUANTILES DO DESKTOP
    @Test
    fun quantiles() {
        Model.setup(File("src/main/assets/model.json").readText())
        val item  = golden.getJSONObject("quantiles")
        val frame = item.getJSONObject("frame")
        val ts    = getLongs(frame.getJSONArray("ts"))
        val clock = getClock(ts, "America/Sao_Paulo")
        val X     = Array(ts.size) { i -> doubleArrayOf(clock.hour[i], clock.weekday[i].toDouble(), *listOf("distance", "duration", "corridor", "rain").map { frame.getJSONArray(it).getDouble(i) }.toDoubleArray()) }
        var worst = 0.0

        for (column in Model.TARGETS.keys) {
            val trees  = Model.boosters!!.getValue(column)
            val sorted = Model.getQuantiles(trees, X)

            Model.QUANTILES.forEachIndexed { q, key ->
                getDoubles(item.getJSONObject("raw").getJSONObject(column).getJSONArray(key)).forEachIndexed { i, value -> worst = maxOf(worst, abs(value - trees[q].sumOf { it.get(X[i]) })) }
                getDoubles(item.getJSONObject("sorted").getJSONArray(column).getJSONArray(q)).forEachIndexed { i, value -> worst = maxOf(worst, abs(value - sorted[q][i])) }
            }
        }

        println("maior diferenca nas arvores e quantis: $worst")
        assertEquals(0.0, worst, 1e-12)
    }

    // SERIE DO MODELO (P10 A M90) IGUAL AO CENTAVO E AO CENTESIMO DE MINUTO EM TODAS AS ROTAS, HORARIOS E REGIMES DE OBSERVACAO, INCLUSIVE COM CHOQUE
    @Test
    fun model() {
        Model.setup(File("src/main/assets/model.json").readText())
        val cases = golden.getJSONArray("model")
        var worst = 0.0

        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val obs  = case.getJSONObject("obs")
            val out  = Model.get(getRoute(case.getJSONObject("route")), getLongs(case.getJSONArray("ts")), getDoubles(case.getJSONArray("rain")), Obs(getLongs(obs.getJSONArray("ts")), getDoubles(obs.getJSONArray("rain")), mapOf("price" to getDoubles(obs.getJSONArray("price")), "minutes" to getDoubles(obs.getJSONArray("minutes")))))!!

            for ((key, values) in out) getDoubles(case.getJSONObject("out").getJSONArray(key)).forEachIndexed { k, value -> worst = maxOf(worst, abs(value - values[k])) }
        }

        println("maior diferenca na serie do modelo em ${cases.length()} casos: $worst")
        assertEquals(0.0, worst, 1e-9)
    }

    // SERIE COMPLETA DO ENGINE (GRADE, CHUVA E CHANCE INTERPOLADAS, PRECO OBSERVADO AGORA E QUANTIS) IGUAL A DO DESKTOP COM O MESMO CLIMA E AS MESMAS OBSERVACOES
    @Test
    fun engine() {
        Model.setup(File("src/main/assets/model.json").readText())
        val cases = golden.getJSONArray("engine")

        for (i in 0 until cases.length()) {
            val case    = cases.getJSONObject(i)
            val weather = case.getJSONObject("weather")
            val today   = case.getJSONObject("today")
            val out     = case.getJSONObject("out")
            val series  = Engine.getSeries(getRoute(case.getJSONObject("route")), case.getLong("now"), Weather(getDoubles(weather.getJSONArray("ts")), getDoubles(weather.getJSONArray("rain")), getDoubles(weather.getJSONArray("probability"))), Obs(getLongs(today.getJSONArray("ts")), getDoubles(today.getJSONArray("rain")), mapOf("price" to getDoubles(today.getJSONArray("price")), "minutes" to getDoubles(today.getJSONArray("minutes")))))!!

            assertTrue(getLongs(out.getJSONArray("ts")).contentEquals(series.ts))
            assertTrue(getDoubles(out.getJSONArray("rain")).contentEquals(series.rain))
            assertTrue(getDoubles(out.getJSONArray("probability")).contentEquals(series.probability))
            assertEquals(out.getJSONArray("price").getDouble(0), series.price, 0.0)
            assertEquals(out.getJSONArray("minutes").getDouble(0), series.minutes, 0.0)
            series.bands.forEach { (key, values) -> assertTrue(key, getDoubles(out.getJSONArray(key)).contentEquals(values)) }
        }
    }

    // PRECO E TEMPO DO ORACULO IGUAIS AO CENTAVO E AO DECIMO DE MINUTO EM MILHARES DE INSTANTES, ROTAS, FUSOS E CHUVAS
    @Test
    fun market() {
        val cases = golden.getJSONArray("market")

        for (i in 0 until cases.length()) {
            val case  = cases.getJSONObject(i)
            val route = getRoute(case.getJSONObject("route"))
            val (price, minutes) = Oracle.getMarket(route, getLongs(case.getJSONArray("ts")), getDoubles(case.getJSONArray("rain")))
            assertTrue(getDoubles(case.getJSONArray("price")).contentEquals(price))
            assertTrue(getDoubles(case.getJSONArray("minutes")).contentEquals(minutes))
        }
    }
}
