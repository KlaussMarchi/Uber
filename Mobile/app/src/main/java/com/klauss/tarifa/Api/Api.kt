package com.klauss.tarifa

import android.util.Log
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.Locale
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener
import kotlin.math.max


// CHUVA HORARIA (MM/H) E CHANCE DE CHUVA (%) CENTRADAS NO MEIO DE CADA HORA, EM UNIX TIME
class Weather(val ts: DoubleArray, val rain: DoubleArray, val probability: DoubleArray)

// CLIENTE DOS SERVICOS ABERTOS (PHOTON, NOMINATIM, OSRM, OPEN-METEO, BEACONDB) COM FILA POR SERVIDOR E RETENTATIVA EM FALHA DE REDE OU 5XX
object Api {
    const val PHOTON    = "https://photon.komoot.io/api/"
    const val REVERSE   = "https://photon.komoot.io/reverse"
    const val NOMINATIM = "https://nominatim.openstreetmap.org/search"
    const val FORECAST  = "https://api.open-meteo.com/v1/forecast"
    const val BEACONDB  = "https://api.beacondb.net/v1/geolocate"
    const val AGENT     = "TarifaDinamica/1.0 (app Android)"
    const val TIMEOUT   = 12_000    // ms de conexao e de leitura
    const val RETRIES   = 2
    const val BACKOFF   = 800L      // ms antes da primeira retentativa, dobrando a cada uma
    const val BIAS      = "&lat=-22.43&lon=-41.85&zoom=12"    // prioriza a regiao de Macae e Rio das Ostras sem esconder o resto do pais
    const val BRAZIL    = "-74.0,-33.8,-34.7,5.3"
    const val MAX_SNAP  = 2000.0    // m do ponto pedido ate a via mais proxima; acima disso nao ha acesso de carro (ilha, mar)
    const val STREET    = 1000.0    // m de precisao abaixo da qual a localizacao vira endereco de rua, e nao so cidade
    const val IP_ERROR  = 5000.0    // m de erro que a posicao por IP tem no melhor caso: ela aponta o bairro do provedor, nunca a rua

    // provedores gratuitos de posicao por IP, sem cadastro; a mediana protege contra um deles errar centenas de km
    val ADDRESSES = listOf("https://get.geojs.io/v1/ip/geo.json", "https://ipwho.is/", "https://ipinfo.io/json")

    val OSRM     = listOf("https://router.project-osrm.org/route/v1/driving/", "https://routing.openstreetmap.de/routed-car/route/v1/driving/")    // demonstracao do projeto e espelho da FOSSGIS com o mesmo mapa; o segundo so entra quando o primeiro falha
    val CORRIDOR = listOf("Rodovia Amaral Peixoto", "RJ-106")

    // milissegundos entre requisicoes por servidor, conforme as politicas de uso publicas
    val INTERVALS = mapOf("nominatim.openstreetmap.org" to 1100L, "router.project-osrm.org" to 1100L, "routing.openstreetmap.de" to 1100L, "photon.komoot.io" to 300L)

    val slots = HashMap<String, Long>()

    fun wait(host: String) {
        val slot = synchronized(slots) {
            val next = max(System.currentTimeMillis(), slots[host] ?: 0L)
            slots[host] = next + (INTERVALS[host] ?: 0L)
            next
        }

        Thread.sleep(max(0L, slot - System.currentTimeMillis()))
    }

    // JSON DE UMA URL, POST QUANDO HA CORPO; FALHA DE REDE, HTTP OU JSON VIRA NULL E NUNCA EXCECAO
    fun get(url: String, body: String? = null): Any? {
        val host = URL(url).host
        wait(host)

        for (attempt in 0..RETRIES) {
            val retry = body == null && attempt < RETRIES

            try {
                val conn = URL(url).openConnection() as HttpURLConnection
                conn.connectTimeout = TIMEOUT
                conn.readTimeout    = TIMEOUT
                conn.setRequestProperty("User-Agent", AGENT)

                if (body != null) {
                    conn.requestMethod = "POST"
                    conn.doOutput      = true
                    conn.setRequestProperty("Content-Type", "application/json")
                    conn.outputStream.use { it.write(body.toByteArray()) }
                }

                val code = conn.responseCode

                if (code >= 500 && retry) {
                    Thread.sleep(BACKOFF shl attempt)
                    continue
                }

                if (code !in 200..299) {
                    Log.w("Api", "falha ao consultar $host: HTTP $code")
                    return null
                }

                return JSONTokener(conn.inputStream.bufferedReader().use { it.readText() }).nextValue()
            } catch (err: IOException) {
                if (!retry) {
                    Log.w("Api", "falha ao consultar $host: $err")
                    return null
                }

                Thread.sleep(BACKOFF shl attempt)
            } catch (err: JSONException) {
                Log.w("Api", "resposta invalida de $host: $err")
                return null
            }
        }

        return null
    }

    // SUGESTOES DO AUTOCOMPLETE; O PHOTON CASA PREFIXOS ("RUA PROFESSOR ANT") E ACEITA BUSCA POR TECLA, O NOMINATIM NAO
    fun search(text: String, limit: Int = 6): List<Place>? {
        val res      = get("$PHOTON?q=${encode(text)}&limit=${limit * 2}&bbox=$BRAZIL$BIAS") as? JSONObject ?: return null
        val features = res.optJSONArray("features") ?: JSONArray()
        return (0 until features.length()).map(features::getJSONObject).filter { getText(it.getJSONObject("properties"), "countrycode") == "BR" }.map(::getPlace).distinctBy { it.label }.take(limit)
    }

    // VALIDA TEXTO DIGITADO SEM ESCOLHER SUGESTAO; E UMA CONSULTA UNICA, ENTAO O NOMINATIM PODE ENTRAR COMO SEGUNDA FONTE
    fun geocode(text: String): Place? {
        search(text, 1)?.firstOrNull()?.let { return it }
        val res = (get("$NOMINATIM?q=${encode(text)}&format=jsonv2&limit=1&countrycodes=br") as? JSONArray)?.optJSONObject(0) ?: return null
        return Place(res.getString("display_name").split(", ").take(3).joinToString(", "), res.getString("lat").toDouble(), res.getString("lon").toDouble())
    }

    // POSICAO APROXIMADA SEM GPS: O BEACONDB E OS PROVEDORES DE IP; VALE A FONTE MAIS PRECISA, COMO NO DESKTOP
    fun locate(): Place? {
        val found = listOfNotNull(getBeacon(), getAddress()).minByOrNull { it.accuracy ?: IP_ERROR } ?: return null
        val error = found.accuracy ?: IP_ERROR
        return reverse(found.lat!!, found.lon!!, error <= STREET).copy(accuracy = error)
    }

    // POSICAO NO BEACONDB, QUE E ABERTO E DEVOLVE A PRECISAO EM METROS; SEM AS REDES DO CELULAR ELE RESPONDE PELO IP E DECLARA DEZENAS DE KM
    fun getBeacon(): Place? {
        val res      = get(BEACONDB, "{}") as? JSONObject ?: return null
        val location = res.optJSONObject("location") ?: return null
        return Place("", location.getDouble("lat"), location.getDouble("lng"), res.optDouble("accuracy", 0.0).takeIf { it > 0 } ?: IP_ERROR)
    }

    // POSICAO PELO IP: A MEDIANA DOS PROVEDORES QUE RESPONDEREM, COM A DISPERSAO ENTRE ELES COMO ERRO DECLARADO
    fun getAddress(): Place? {
        val found = ADDRESSES.parallelStream().map(::getCoords).toList().filterNotNull()

        if (found.isEmpty()) return null

        val lat    = Oracle.getMedian(found.map { it.first })
        val lon    = Oracle.getMedian(found.map { it.second })
        val spread = found.maxOf { getDistance(lat, lon, it.first, it.second) } * 1000
        return Place("", lat, lon, max(spread, IP_ERROR))
    }

    // COORDENADA DE UM PROVEDOR DE IP; RESPOSTA AUSENTE, FORA DO GLOBO OU NO PONTO NULO NAO CONTA
    fun getCoords(url: String): Pair<Double, Double>? {
        val res   = get(url) as? JSONObject ?: return null
        val point = getText(res, "loc").split(",")
        val lat   = (res.opt("latitude")?.toString() ?: point.getOrNull(0))?.toDoubleOrNull() ?: return null
        val lon   = (res.opt("longitude")?.toString() ?: point.getOrNull(1))?.toDoubleOrNull() ?: return null
        return if (lat in -90.0..90.0 && lon in -180.0..180.0 && (lat != 0.0 || lon != 0.0)) lat to lon else null
    }

    // ENDERECO DE UMA COORDENADA (PHOTON REVERSO); COM ESTIMATIVA GROSSEIRA MOSTRA SO A CIDADE, PARA NAO SUGERIR UMA RUA ERRADA
    fun reverse(lat: Double, lon: Double, street: Boolean = true): Place {
        val item  = ((get("$REVERSE?lat=$lat&lon=$lon&limit=1") as? JSONObject)?.optJSONArray("features"))?.optJSONObject(0)
        val props = item?.getJSONObject("properties")
        val label = if (item != null && street) getPlace(item).label else listOf("city", "state").map { if (props == null) "" else getText(props, it) }.filter(String::isNotEmpty).joinToString(", ")
        return Place(label.ifEmpty { String.format(Locale.US, "%.5f, %.5f", lat, lon) }, lat, lon)
    }

    // ROTULO LEGIVEL DE UM RESULTADO DO PHOTON: NOME, RUA E NUMERO, BAIRRO, CIDADE E ESTADO, SEM REPETIR PARTES
    fun getPlace(item: JSONObject): Place {
        val props  = item.getJSONObject("properties")
        val coords = item.getJSONObject("geometry").getJSONArray("coordinates")
        val street = listOf("street", "housenumber").map { getText(props, it) }.filter(String::isNotEmpty).joinToString(", ")
        val parts  = listOf(getText(props, "name"), street, getText(props, "district"), getText(props, "city"), getText(props, "state"))
        return Place(parts.filter(String::isNotEmpty).distinct().joinToString(", "), coords.getDouble(1), coords.getDouble(0))
    }

    // ROTA DE CARRO NO OSRM (COM O ESPELHO COMO RESERVA): DISTANCIA KM, DURACAO MIN E FRACAO NA RJ-106; PONTO LONGE DE QUALQUER VIA NAO TEM ROTA
    fun getRoute(src: Place, dst: Place): Triple<Double, Double, Double>? {
        val path = "${src.lon},${src.lat};${dst.lon},${dst.lat}"
        val res  = OSRM.firstNotNullOfOrNull { get("$it$path?overview=false&steps=true") as? JSONObject } ?: return null
        val waypoints = res.optJSONArray("waypoints") ?: return null

        if (res.optString("code") != "Ok" || (res.optJSONArray("routes")?.length() ?: 0) == 0 || (0 until waypoints.length()).any { waypoints.getJSONObject(it).getDouble("distance") > MAX_SNAP }) return null

        val route = res.getJSONArray("routes").getJSONObject(0)
        val legs  = route.getJSONArray("legs")
        var onCorridor = 0.0

        for (i in 0 until legs.length()) {
            val steps = legs.getJSONObject(i).getJSONArray("steps")

            for (k in 0 until steps.length()) {
                val step = steps.getJSONObject(k)
                if (CORRIDOR.any { "${getText(step, "name")} ${getText(step, "ref")}".contains(it) }) onCorridor += step.getDouble("distance")
            }
        }

        return Triple(route.getDouble("distance") / 1000, route.getDouble("duration") / 60, onCorridor / max(route.getDouble("distance"), 1.0))
    }

    // FUSO IANA DE UMA COORDENADA; O BRASIL TEM QUATRO FUSOS E A DEMANDA SEGUE A HORA LOCAL DA ROTA
    fun getZone(lat: Double, lon: Double) = (get("$FORECAST?latitude=$lat&longitude=$lon&timezone=auto") as? JSONObject)?.let { getText(it, "timezone") }?.ifEmpty { null }

    // CHUVA E CHANCE DE CHUVA DE ONTEM ATE DEPOIS DE AMANHA; O VALOR HORARIO E A SOMA DA HORA ANTERIOR, ENTAO VALE NO MEIO DELA
    fun getWeather(lat: Double, lon: Double): Weather? {
        val res    = get("$FORECAST?latitude=$lat&longitude=$lon&hourly=precipitation,precipitation_probability&timeformat=unixtime&past_days=1&forecast_days=2") as? JSONObject ?: return null
        val hourly = res.optJSONObject("hourly") ?: return null
        val time   = hourly.getJSONArray("time")
        val rain   = hourly.getJSONArray("precipitation")
        val chance = hourly.optJSONArray("precipitation_probability")
        val rows   = (0 until time.length()).filter { !rain.isNull(it) }

        if (rows.isEmpty()) return null

        return Weather(DoubleArray(rows.size) { time.getLong(rows[it]) - 1800.0 }, DoubleArray(rows.size) { rain.getDouble(rows[it]) }, DoubleArray(rows.size) { if (chance == null || chance.isNull(rows[it])) Double.NaN else chance.getDouble(rows[it]) })
    }

    fun encode(text: String): String = URLEncoder.encode(text, "UTF-8")

    // TEXTO DE UM CAMPO JSON; AUSENTE OU NULL VIRA VAZIO (O ORG.JSON DO ANDROID DEVOLVERIA "null")
    fun getText(item: JSONObject, key: String) = if (item.isNull(key)) "" else item.get(key).toString()
}
