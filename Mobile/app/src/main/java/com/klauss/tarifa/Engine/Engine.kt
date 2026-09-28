package com.klauss.tarifa

import android.content.Context


// SERIE DE UMA CONSULTA: INSTANTES (O PRIMEIRO E AGORA), CHUVA E CHANCE PREVISTAS, QUANTIS DE PRECO E TEMPO E O PRECO, O TEMPO E A DINAMICA ESPERADOS AGORA
class Series(val ts: LongArray, val rain: DoubleArray, val probability: DoubleArray, val bands: Map<String, DoubleArray>, val price: Double, val minutes: Double, val surge: Double)

// RESPOSTA DA CONSULTA COMPLETA PARA A TELA: ENDERECOS VALIDADOS, ROTA E SERIE, OU O MOTIVO DA FALHA
class Quote(val src: Place? = null, val dst: Place? = null, val route: Route? = null, val series: Series? = null, val error: String? = null)

// NUCLEO DA CONSULTA: ROTA, CLIMA, PRECO E TEMPO ESPERADOS AGORA E SERIE DE 12 H CALIBRADA PELOS PRECOS INFORMADOS; USADO PELA TELA, PELO WORKER E PELO ACOMPANHAMENTO
object Engine {
    const val WEATHER_TTL  = 600_000L    // ms de validade da previsao do tempo em cache
    const val MIN_DISTANCE = 0.3         // km abaixo do qual origem e destino sao o mesmo ponto
    const val WHERE        = "WHERE abs(o_lat - ?) < 1e-7 AND abs(o_lon - ?) < 1e-7 AND abs(d_lat - ?) < 1e-7 AND abs(d_lon - ?) < 1e-7"    // o android so passa argumento como texto, entao a coordenada e comparada com folga

    val weather = HashMap<String, Pair<Long, Weather>>()

    // TABELA AJUSTADA AS MEDIAS REAIS (ASSETS) E PRECOS JA INFORMADOS; A TELA E O ACOMPANHAMENTO CHAMAM, E O QUE SUBIR PRIMEIRO CARREGA
    fun setup(context: Context) {
        if (!Oracle.ready()) Oracle.load(context.assets.open("markets.json").bufferedReader().use { it.readText() })
        update()
    }

    fun update() = Oracle.update(Database.get("SELECT company, ts, lat, lon, city, uf, level, expected, observed FROM Fares ORDER BY ts, id") { Ride(it.getString(0), it.getLong(1), it.getDouble(2), it.getDouble(3), it.getString(4), it.getString(5), it.getDouble(6), it.getDouble(7), it.getDouble(8)) })

    // ROTA COM CACHE NO BANCO, FUSO E MUNICIPIO DA ORIGEM; MARCA O USO PORQUE O WORKER OBSERVA AS ROTAS MAIS RECENTES
    fun getRoute(src: Place, dst: Place): Route? {
        val key = arrayOf<Any>(getRound(src.lat!!, 5), getRound(src.lon!!, 5), getRound(dst.lat!!, 5), getRound(dst.lon!!, 5))

        if (Database.get("SELECT id FROM Routes $WHERE", *key) { it.getLong(0) }.isEmpty()) {
            val res  = Api.getRoute(src, dst)
            val zone = res?.let { Api.getZone(src.lat, src.lon) }
            if (res == null || zone == null || res.first < MIN_DISTANCE) return null
            Database.set("INSERT OR IGNORE INTO Routes (origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, tz, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)", listOf(arrayOf(src.label, dst.label, *key, res.first, res.second, zone)))
        }

        Database.set("UPDATE Routes SET used_at = ? $WHERE", listOf(arrayOf(System.currentTimeMillis() / 1000, *key)))
        val route = Database.get("SELECT ${Database.COLUMNS} FROM Routes $WHERE", *key, fn = Database::getRoute).firstOrNull() ?: return null

        if (route.city.isNotEmpty()) return route

        val place = Api.getCity(route.oLat, route.oLon) ?: return route    // sem o municipio vale a vizinhanca; a proxima consulta tenta de novo
        Database.set("UPDATE Routes SET city = ?, uf = ? WHERE id = ?", listOf(arrayOf<Any>(place.first, place.second, route.id)))
        return route.copy(city = place.first, uf = place.second)
    }

    // LUGARES QUE O USUARIO JA CONFIRMOU, DOS MAIS RECENTES
    fun getPlaces(limit: Int = 5) = Database.get("SELECT label, lat, lon FROM (SELECT origin AS label, o_lat AS lat, o_lon AS lon, used_at FROM Routes UNION ALL SELECT destination, d_lat, d_lon, used_at FROM Routes) WHERE used_at > 0 ORDER BY used_at DESC") { Place(it.getString(0), it.getDouble(1), it.getDouble(2)) }.distinctBy { it.label }.take(limit)

    // PREVISAO DE CHUVA POR CELULA DE ~1 KM COM CACHE CURTO; SE A API FALHA, SEGUE COM A ULTIMA PREVISAO RECEBIDA
    fun getWeather(lat: Double, lon: Double): Weather? {
        val key = "${getRound(lat, 2)},${getRound(lon, 2)}"
        val hit = synchronized(weather) { weather[key] }

        if (hit != null && System.currentTimeMillis() - hit.first < WEATHER_TTL) return hit.second

        val res = Api.getWeather(getRound(lat, 2), getRound(lon, 2)) ?: return hit?.second
        synchronized(weather) { weather[key] = System.currentTimeMillis() to res }
        return res
    }

    // CHUVA E CHANCE NA GRADE DA SERIE: A LINHA 0 E O INSTANTE EXATO, AS DEMAIS SAO OS SLOTS DE 10 MIN ATE 12 H
    fun getGrid(now: Long, weather: Weather): Triple<LongArray, DoubleArray, DoubleArray>? {
        val ts = longArrayOf(now) + LongArray((HORIZON / STEP).toInt()) { (now / STEP + 1 + it) * STEP }

        // previsao em cache que ja nao cobre o horizonte viraria chuva constante no fim da serie
        if (weather.ts.last() < ts.last()) return null

        return Triple(ts, DoubleArray(ts.size) { getInterp(ts[it].toDouble(), weather.ts, weather.rain) }, DoubleArray(ts.size) { getInterp(ts[it].toDouble(), weather.ts, weather.probability) })
    }

    fun getSeries(route: Route, now: Long, weather: Weather, company: String): Series? {
        val (ts, rain, chance) = getGrid(now, weather) ?: return null
        val bands  = Model.get(route, ts, rain, chance, company, Oracle.getCalibration(route, company, now))
        val excess = Oracle.getState(route, longArrayOf(now), doubleArrayOf(rain[0])).excess[0]
        return Series(ts, rain, chance, bands, bands.getValue("p50")[0], bands.getValue("m50")[0], Oracle.getSurge(excess, company))
    }

    fun get(route: Route, company: String, now: Long = System.currentTimeMillis() / 1000): Series? {
        val weather = getWeather(route.oLat, route.oLon) ?: return null
        return getSeries(route, now, weather, company)
    }

    // PRECO REAL LIDO NO APLICATIVO: GUARDA COM O PRECO CENTRAL QUE O APP MOSTRARIA SEM CALIBRACAO NAQUELE INSTANTE E RECALIBRA; ZERO APAGA OS PRECOS DO APLICATIVO
    fun setFare(route: Route, company: String, observed: Double, now: Long = System.currentTimeMillis() / 1000): Int {
        if (observed <= 0) {
            Database.set("DELETE FROM Fares WHERE company = ?", listOf(arrayOf(company)))
        } else {
            val (_, rain, chance) = getGrid(now, getWeather(route.oLat, route.oLon) ?: return -1) ?: return -1
            val anchor   = Oracle.getAnchor(route, now)
            val expected = Model.getBands(route, longArrayOf(now), doubleArrayOf(rain[0]), doubleArrayOf(chance[0]), company, anchor).getValue("p50")[0]
            Database.set("INSERT INTO Fares (company, ts, route_id, lat, lon, city, uf, level, expected, observed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", listOf(arrayOf<Any?>(company, now, route.id, route.oLat, route.oLon, route.city, route.uf, anchor.market.level, expected, observed)))
        }

        update()
        return Database.get("SELECT COUNT(*) FROM Fares WHERE company = ?", company) { it.getInt(0) }.first()
    }

    // CONSULTA COMPLETA: VALIDA OS ENDERECOS, TRACA A ROTA E PREVE; QUANDO UMA ETAPA FALHA DEVOLVE O MOTIVO PARA A TELA
    fun getQuote(src: Place, dst: Place, company: String): Quote {
        if (!Oracle.ready()) return Quote(error = "Carregando a tabela de preços; tente de novo em instantes.")

        val origin = if (src.lat != null) src else Api.geocode(src.label) ?: return Quote(error = "Origem não encontrada. Escolha uma das sugestões da lista.")
        val destination = if (dst.lat != null) dst else Api.geocode(dst.label) ?: return Quote(error = "Destino não encontrado. Escolha uma das sugestões da lista.")

        if (getDistance(origin.lat!!, origin.lon!!, destination.lat!!, destination.lon!!) < MIN_DISTANCE) return Quote(error = "Origem e destino são praticamente o mesmo ponto.")

        val route  = getRoute(origin, destination) ?: return Quote(error = "Sem rota de carro entre esses pontos (ilha, mar ou via inacessível) ou OSRM/Open-Meteo fora do ar.")
        val series = get(route, company) ?: return Quote(error = "Previsão do tempo indisponível (Open-Meteo), tente novamente em instantes.")
        return Quote(origin, destination, route, series)
    }
}
