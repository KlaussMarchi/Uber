package com.klauss.tarifa


// SERIE DE UMA CONSULTA: INSTANTES (O PRIMEIRO E AGORA), CHUVA E CHANCE PREVISTAS, QUANTIS DE PRECO E TEMPO E O QUE O MERCADO MOSTRA AGORA
class Series(val ts: LongArray, val rain: DoubleArray, val probability: DoubleArray, val bands: Map<String, DoubleArray>, val price: Double, val minutes: Double)

// RESPOSTA DA CONSULTA COMPLETA PARA A TELA: ENDERECOS VALIDADOS, ROTA E SERIE, OU O MOTIVO DA FALHA
class Quote(val src: Place? = null, val dst: Place? = null, val route: Route? = null, val series: Series? = null, val error: String? = null)

// NUCLEO DA CONSULTA: ROTA, CLIMA, PRECO OBSERVADO AGORA E SERIE DE 12 H ANCORADA NAS OBSERVACOES DE HOJE; USADO PELA TELA, PELO WORKER E PELO ACOMPANHAMENTO
object Engine {
    const val WEATHER_TTL  = 600_000L    // ms de validade da previsao do tempo em cache
    const val MIN_DISTANCE = 0.3         // km abaixo do qual origem e destino sao o mesmo ponto
    const val WHERE        = "WHERE abs(o_lat - ?) < 1e-7 AND abs(o_lon - ?) < 1e-7 AND abs(d_lat - ?) < 1e-7 AND abs(d_lon - ?) < 1e-7"    // o android so passa argumento como texto, entao a coordenada e comparada com folga

    val weather = HashMap<String, Pair<Long, Weather>>()

    // ROTA COM CACHE NO BANCO E FUSO DA ORIGEM; MARCA O USO PORQUE O WORKER OBSERVA AS ROTAS MAIS RECENTES
    fun getRoute(src: Place, dst: Place): Route? {
        val key = arrayOf<Any>(getRound(src.lat!!, 5), getRound(src.lon!!, 5), getRound(dst.lat!!, 5), getRound(dst.lon!!, 5))

        if (Database.get("SELECT id FROM Routes $WHERE", *key) { it.getLong(0) }.isEmpty()) {
            val res  = Api.getRoute(src, dst)
            val zone = res?.let { Api.getZone(src.lat, src.lon) }
            if (res == null || zone == null || res.first < MIN_DISTANCE) return null
            Database.set("INSERT OR IGNORE INTO Routes (origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, corridor, tz, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)", listOf(arrayOf(src.label, dst.label, *key, res.first, res.second, res.third, zone)))
        }

        Database.set("UPDATE Routes SET used_at = ? $WHERE", listOf(arrayOf(System.currentTimeMillis() / 1000, *key)))
        return Database.get("SELECT id, origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, corridor, tz FROM Routes $WHERE", *key, fn = Database::getRoute).firstOrNull()
    }

    // LUGARES QUE O USUARIO JA CONFIRMOU, DOS MAIS RECENTES; ROTA COM USED_AT 0 NUNCA FOI ESCOLHIDA (COMO AS DO BOOTSTRAP DO DESKTOP) E FICA DE FORA
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

    // SERIE A PARTIR DO CLIMA E DAS OBSERVACOES DE HOJE: A LINHA 0 E O INSTANTE EXATO, AS DEMAIS SAO OS SLOTS DE 10 MIN ATE 12 H
    fun getSeries(route: Route, now: Long, weather: Weather, today: Obs): Series? {
        val ts = longArrayOf(now) + LongArray((HORIZON / STEP).toInt()) { (now / STEP + 1 + it) * STEP }

        // previsao em cache que ja nao cobre o horizonte viraria chuva constante no fim da serie
        if (weather.ts.last() < ts.last() || !Model.ready()) return null

        val rain   = DoubleArray(ts.size) { getInterp(ts[it].toDouble(), weather.ts, weather.rain) }
        val chance = DoubleArray(ts.size) { getInterp(ts[it].toDouble(), weather.ts, weather.probability) }
        val (price, minutes) = Oracle.getMarket(route, longArrayOf(now), doubleArrayOf(rain[0]))
        val obs    = Obs(today.ts + now, today.rain + rain[0], mapOf("price" to today.values.getValue("price") + price[0], "minutes" to today.values.getValue("minutes") + minutes[0]))
        val bands  = Model.get(route, ts, rain, obs) ?: return null
        return Series(ts, rain, chance, bands, price[0], minutes[0])
    }

    fun get(route: Route, now: Long = System.currentTimeMillis() / 1000): Series? {
        val weather  = getWeather(route.oLat, route.oLon) ?: return null
        val midnight = getMidnight(getClock(longArrayOf(now), route.tz).day[0], route.tz)
        val rows     = Database.get("SELECT ts, rain, price, minutes FROM Prices WHERE route_id = ? AND ts >= ? AND ts < ? ORDER BY ts", route.id, midnight, now / STEP * STEP) { it.getLong(0) to doubleArrayOf(it.getDouble(1), it.getDouble(2), it.getDouble(3)) }
        val today    = Obs(LongArray(rows.size) { rows[it].first }, DoubleArray(rows.size) { rows[it].second[0] }, mapOf("price" to DoubleArray(rows.size) { rows[it].second[1] }, "minutes" to DoubleArray(rows.size) { rows[it].second[2] }))
        return getSeries(route, now, weather, today)
    }

    // CONSULTA COMPLETA: VALIDA OS ENDERECOS, TRACA A ROTA E PREVE; QUANDO UMA ETAPA FALHA DEVOLVE O MOTIVO PARA A TELA
    fun getQuote(src: Place, dst: Place): Quote {
        if (!Model.ready()) return Quote(error = "Modelo em preparação: a previsão aparece assim que ele terminar de carregar.")

        val origin = if (src.lat != null) src else Api.geocode(src.label) ?: return Quote(error = "Origem não encontrada. Escolha uma das sugestões da lista.")
        val destination = if (dst.lat != null) dst else Api.geocode(dst.label) ?: return Quote(error = "Destino não encontrado. Escolha uma das sugestões da lista.")

        if (getDistance(origin.lat!!, origin.lon!!, destination.lat!!, destination.lon!!) < MIN_DISTANCE) return Quote(error = "Origem e destino são praticamente o mesmo ponto.")

        val route  = getRoute(origin, destination) ?: return Quote(error = "Sem rota de carro entre esses pontos (ilha, mar ou via inacessível) ou OSRM/Open-Meteo fora do ar.")
        val series = get(route) ?: return Quote(error = "Previsão do tempo indisponível (Open-Meteo), tente novamente em instantes.")
        return Quote(origin, destination, route, series)
    }
}
