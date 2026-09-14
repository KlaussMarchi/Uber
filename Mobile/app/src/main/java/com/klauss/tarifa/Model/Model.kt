package com.klauss.tarifa

import java.util.stream.IntStream
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.ceil
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min


// OBSERVACOES DE HOJE DE UMA ROTA (A ULTIMA E A DE AGORA), COM PRECO E TEMPO POR NOME DE COLUNA COMO NO DESKTOP
class Obs(val ts: LongArray, val rain: DoubleArray, val values: Map<String, DoubleArray>)

// ESTADO DA CALIBRACAO DE UM ALVO: ENCOLHIMENTO DO NIVEL DO DIA, CORTE DO CHOQUE, ANCORA POR HORIZONTE E OFFSETS POR HORIZONTE E REGIME
class Calibration(val lam: Double, val clip: Double, val beta: Array<DoubleArray>, val offsets: Array<Array<DoubleArray>>)

// ARVORE DO LIGHTGBM EM VETORES PLANOS, LIDA DO MESMO TEXTO QUE O DESKTOP SALVA COM MODEL_TO_STRING
class Tree(val feature: IntArray, val threshold: DoubleArray, val decision: IntArray, val left: IntArray, val right: IntArray, val leaf: DoubleArray, val boundaries: IntArray, val categories: IntArray) {
    // FOLHA DE UMA LINHA PELAS REGRAS DO LIGHTGBM: NUMERICA COM TIPO DE FALTANTE E CATEGORICA POR BITSET
    fun get(x: DoubleArray): Double {
        if (feature.isEmpty()) return leaf[0]
        var node = 0

        while (node >= 0) {
            val kind    = decision[node]
            val missing = (kind shr 2) and 3
            var value   = x[feature[node]]

            node = if (kind and 1 != 0) {
                if (value.isNaN() && missing == 2 || !value.isNaN() && value.toInt() < 0) right[node] else {
                    val category = if (value.isNaN()) 0 else value.toInt()
                    val start    = boundaries[threshold[node].toInt()]
                    val words    = boundaries[threshold[node].toInt() + 1] - start
                    if (category / 32 < words && (categories[start + category / 32] ushr (category % 32)) and 1 != 0) left[node] else right[node]
                }
            } else {
                if (value.isNaN() && missing != 2) value = 0.0
                val default = missing == 1 && value >= -1e-35 && value <= 1e-35 || missing == 2 && value.isNaN()
                if (default) (if (kind and 2 != 0) left[node] else right[node]) else if (value <= threshold[node]) left[node] else right[node]
            }
        }

        return leaf[node.inv()]
    }
}

// QUANTIS DO PRECO E DO TEMPO DE VIAGEM TREINADOS NO DESKTOP, ANCORADOS NO QUE O MERCADO MOSTRA AGORA E NO NIVEL DO DIA
object Model {
    val QUANTILES = listOf("10", "50", "90")
    val TARGETS   = mapOf("price" to "p", "minutes" to "m")
    val RAIN_GRID = doubleArrayOf(0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0)    // mm/h, grade do rearranjo monotono
    val REGIMES   = intArrayOf(1, 2, 6)                                         // observacoes de hoje que abrem cada regime: rota nova, rastreada ha menos de 1 h, rastreada
    val DIGITS    = mapOf("p" to 2, "m" to 2)
    val LEADS     = (HORIZON / STEP + 1).toInt()
    const val MIN_BAND = 0.01                                                   // largura minima da faixa, fracao da mediana

    var boosters: Map<String, List<List<Tree>>>? = null
    var state     = emptyMap<String, Calibration>()
    var version   = 0
    var trainedAt = 0L

    // LE O MODEL.JSON DO DESKTOP; ARQUIVO FORA DO FORMATO E ERRO DE EMPACOTAMENTO, ENTAO FALHA ALTO
    fun setup(text: String) = synchronized(this) {
        if (boosters != null) return@synchronized

        val data = JSONObject(text)
        val fits = TARGETS.keys.associateWith { column ->
            val item = data.getJSONObject("state").getJSONObject(column)
            val beta = item.getJSONArray("beta").let { rows -> Array(rows.length()) { getDoubles(rows.getJSONArray(it)) } }
            val offsets = item.getJSONArray("offsets").let { rows -> Array(rows.length()) { h -> rows.getJSONArray(h).let { regimes -> Array(regimes.length()) { getDoubles(regimes.getJSONArray(it)) } } } }
            check(beta.size == LEADS && beta.all { it.size == 4 } && offsets.size == LEADS && offsets.all { row -> row.size == REGIMES.size && row.all { it.size == 2 } }) { "model.json com horizontes diferentes do app" }
            Calibration(item.getDouble("lam"), item.getDouble("clip"), beta, offsets)
        }

        state     = fits
        version   = data.getInt("version")
        trainedAt = data.getJSONObject("metrics").getLong("trained_at")
        boosters  = TARGETS.keys.associateWith { column -> QUANTILES.map { getTrees(data.getJSONObject("boosters").getJSONObject(column).getString(it)) } }
    }

    fun ready() = boosters != null

    // SERIE PREVISTA PARA OS INSTANTES PEDIDOS: QUANTIS DO MULTIPLICADOR, DESLOCADOS PELA ANCORA DE HOJE E ABERTOS PELOS OFFSETS CONFORMAIS
    fun get(route: Route, ts: LongArray, rain: DoubleArray, obs: Obs): Map<String, DoubleArray>? {
        val trees = boosters ?: return null
        val both  = ts + obs.ts
        val clock = getClock(both, route.tz)
        val wet   = rain + obs.rain
        val X     = Array(both.size) { doubleArrayOf(clock.hour[it], clock.weekday[it].toDouble(), route.distance, route.duration, route.corridor, wet[it]) }
        val n     = ts.size
        val out   = LinkedHashMap<String, DoubleArray>()

        for ((column, prefix) in TARGETS) {
            val fit    = state.getValue(column)
            val scale  = if (column == "price") Oracle.getTariff(route.distance, route.duration) else route.duration
            val (lo, mid, hi) = getQuantiles(trees.getValue(column), X)
            val r      = DoubleArray(obs.ts.size) { ln(obs.values.getValue(column)[it] / scale / mid[n + it]) }
            val clip   = DoubleArray(r.size) { r[it].coerceIn(-fit.clip, fit.clip) }
            val sum    = clip.sum()
            val regime = REGIMES.count { it <= r.size } - 1
            val bands  = Array(3) { DoubleArray(n) }

            for (i in 0 until n) {
                val lead   = ceil((ts[i] - obs.ts.last()).toDouble() / STEP).coerceIn(0.0, LEADS - 1.0).toInt()
                val b      = fit.beta[lead]
                val same   = if (clock.day[i] == clock.day.last()) 1.0 else 0.0
                val shift  = exp(b[0] + b[1] * clip.last() + b[2] * (r.last() - clip.last()) + b[3] * sum / (r.size + fit.lam) * same)
                val side   = fit.offsets[lead][regime]
                val middle = mid[i] * shift
                bands[0][i] = min(lo[i] * shift - side[0], middle * (1 - MIN_BAND / 2)) * scale
                bands[1][i] = middle * scale
                bands[2][i] = max(hi[i] * shift + side[1], middle * (1 + MIN_BAND / 2)) * scale
            }

            QUANTILES.forEachIndexed { q, key -> out["$prefix$key"] = DoubleArray(n) { getRound(bands[q][it], DIGITS.getValue(prefix)) } }
        }

        return out
    }

    // QUANTIS DO MULTIPLICADOR REARRANJADOS NA GRADE DE CHUVA (MAIS CHUVA NUNCA BARATEIA NEM ACELERA) E ORDENADOS PARA NUNCA CRUZAREM
    fun getQuantiles(trees: List<List<Tree>>, X: Array<DoubleArray>): Array<DoubleArray> {
        val k   = RAIN_GRID.size
        val out = Array(3) { DoubleArray(X.size) }

        // linhas independentes em paralelo: cada uma so escreve a sua posicao, entao o resultado nao depende da ordem
        IntStream.range(0, X.size).parallel().forEach { i ->
            val row    = X[i].copyOf()
            val rain   = row[5].coerceIn(0.0, RAIN_GRID.last())
            val j      = (RAIN_GRID.count { it <= rain } - 1).coerceIn(0, k - 2)
            val w      = (rain - RAIN_GRID[j]) / (RAIN_GRID[j + 1] - RAIN_GRID[j])

            val values = DoubleArray(3) { q ->
                val curve = DoubleArray(k) { g -> row[5] = RAIN_GRID[g]; trees[q].sumOf { it.get(row) } }
                curve.sort()
                curve[j] * (1 - w) + curve[j + 1] * w
            }

            values.sort()
            for (q in 0 until 3) out[q][i] = values[q]
        }

        return out
    }

    // ARVORES DE UM BOOSTER A PARTIR DO TEXTO DO LIGHTGBM, BLOCO "TREE=" POR BLOCO
    fun getTrees(text: String) = text.substringBefore("end of trees").split("\nTree=").drop(1).map { block ->
        val fields = block.lines().mapNotNull { line -> line.indexOf('=').takeIf { it > 0 }?.let { line.substring(0, it) to line.substring(it + 1) } }.toMap()
        val tokens = { key: String -> fields[key].orEmpty().split(' ').filter(String::isNotEmpty) }
        Tree(tokens("split_feature").map(String::toInt).toIntArray(), tokens("threshold").map(String::toDouble).toDoubleArray(), tokens("decision_type").map(String::toInt).toIntArray(), tokens("left_child").map(String::toInt).toIntArray(), tokens("right_child").map(String::toInt).toIntArray(), tokens("leaf_value").map(String::toDouble).toDoubleArray(), tokens("cat_boundaries").map(String::toInt).toIntArray(), tokens("cat_threshold").map { it.toLong().toInt() }.toIntArray())
    }

    fun getDoubles(array: JSONArray) = DoubleArray(array.length()) { array.getDouble(it) }
}
