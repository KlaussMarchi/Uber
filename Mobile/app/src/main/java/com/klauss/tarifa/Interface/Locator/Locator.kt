package com.klauss.tarifa

import android.graphics.BitmapFactory
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import kotlin.math.PI
import kotlin.math.atan
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sinh
import kotlin.math.tan
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext


// ESCOLHA DO PONTO EXATO: A LOCALIZACAO AUTOMATICA PODE ERRAR, ENTAO VALEM OS LUGARES JA USADOS E O TOQUE NUM MAPA ESCURO QUE SE ARRASTA
class Locator(estimate: Place?, val recent: List<Place>, val cb: (Place) -> Unit, val onCancel: () -> Unit) {
    companion object {
        val TILES = listOf("https://tile.opentopomap.org/%d/%d/%d.png", "https://a.tile.openstreetmap.fr/osmfr/%d/%d/%d.png")    // opentopomap e, se ele falhar, a osm france; os servidores principais do OSM recusam app sem cadastro

        const val TILE   = 180      // dp de cada tile na tela; o png de 256 px segue legivel num celular de alta densidade
        const val HEIGHT = 380      // dp do mapa
        const val NEAR   = 15       // zoom ao abrir num lugar ja usado: nivel de rua, para so ajustar o ponto
        const val PINCH  = 1.6f     // escala acumulada da pinca que sobe ou desce um nivel de zoom
        const val DELAY  = 500L     // ms de pausa antes de buscar o endereco do ponto marcado
        const val PLACES = 5

        val ZOOMS  = listOf(500.0 to 16, 5000.0 to 14, Double.POSITIVE_INFINITY to 12)    // zoom inicial conforme o erro da estimativa: quanto pior, mais aberto
        val LIMITS = 11..17                                                                // o opentopomap renderiza ate o zoom 17
        val REGION = Place("Macaé e Rio das Ostras", -22.43, -41.85)                      // o mesmo centro que prioriza a regiao no autocomplete

        // como o mapa abriu, na primeira linha da janela
        val NOTES = mapOf(
            "estimativa" to "Estimativa automática com erro de ~%s.",
            "perto"      to "Estimativa automática com erro de ~%s: o mapa abriu no último lugar usado dentro dessa área.",
            "lugar"      to "Localização automática indisponível: o mapa abriu no último lugar usado.",
            "regiao"     to "Localização automática indisponível.",
        )

        // cinza invertido deixa o mapa escuro como o resto do app, com as ruas claras
        val FILTER = ColorFilter.colorMatrix(ColorMatrix(floatArrayOf(-0.299f, -0.587f, -0.114f, 0f, 255f, -0.299f, -0.587f, -0.114f, 0f, 255f, -0.299f, -0.587f, -0.114f, 0f, 255f, 0f, 0f, 0f, 1f, 0f)))

        // PONTO INICIAL, ZOOM E MODO: A ESTIMATIVA; SE ELA E GROSSEIRA, O LUGAR USADO MAIS RECENTE DENTRO DO ERRO DELA; SEM ESTIMATIVA, O ULTIMO LUGAR USADO OU A REGIAO
        fun getStart(estimate: Place?, recent: List<Place>): Triple<Place, Int, String> {
            if (estimate == null) return if (recent.isEmpty()) Triple(REGION, ZOOMS.last().second, "regiao") else Triple(recent[0], NEAR, "lugar")

            val accuracy = estimate.accuracy ?: 0.0
            val near     = if (accuracy > Api.STREET) recent.firstOrNull { getDistance(estimate.lat!!, estimate.lon!!, it.lat!!, it.lon!!) * 1000 <= accuracy } else null

            if (near != null) return Triple(near, NEAR, "perto")

            return Triple(estimate, ZOOMS.first { accuracy <= it.first }.second, "estimativa")
        }
    }

    val start    = getStart(estimate, recent)
    val accuracy = estimate?.accuracy ?: 0.0
    var place    = start.first
    var zoom    by mutableIntStateOf(start.second)
    var center  by mutableStateOf(place.lat!! to place.lon!!)
    var marker  by mutableStateOf(place.lat!! to place.lon!!)
    var address by mutableStateOf<String?>(place.label)
    var taps    by mutableIntStateOf(0)
    var pinch    = 1f
    val tiles    = mutableStateMapOf<Triple<Int, Int, Int>, ImageBitmap>()
    val pending  = HashSet<Triple<Int, Int, Int>>()

    // PIXEL GLOBAL DE UMA COORDENADA NO NIVEL DE ZOOM (WEB MERCATOR, A PROJECAO DOS TILES)
    fun getPixel(lat: Double, lon: Double, zoom: Int, tile: Float): Pair<Double, Double> {
        val size = tile * 2.0.pow(zoom)
        val rad  = Math.toRadians(lat)
        return (lon + 180) / 360 * size to (1 - ln(tan(rad) + 1 / cos(rad)) / PI) / 2 * size
    }

    // COORDENADA DE VOLTA A PARTIR DO PIXEL GLOBAL
    fun getCoords(px: Double, py: Double, zoom: Int, tile: Float): Pair<Double, Double> {
        val size = tile * 2.0.pow(zoom)
        return Math.toDegrees(atan(sinh(PI * (1 - 2 * py / size)))) to px / size * 360 - 180
    }

    // BAIXA UM TILE FORA DA THREAD DA TELA, TENTANDO OS SERVIDORES EM ORDEM; FALHA DEIXA O QUADRADO VAZIO, QUE E PEDIDO DE NOVO QUANDO O MAPA SE MOVE
    fun getTile(key: Triple<Int, Int, Int>): ImageBitmap? {
        for (url in TILES) {
            try {
                val conn = URL(String.format(Locale.US, url, key.first, key.second, key.third)).openConnection() as HttpURLConnection
                conn.connectTimeout = Api.TIMEOUT
                conn.readTimeout    = Api.TIMEOUT
                conn.setRequestProperty("User-Agent", Api.AGENT)

                if (conn.responseCode !in 200..299) continue

                conn.inputStream.use(BitmapFactory::decodeStream)?.let { return it.asImageBitmap() }
            } catch (err: IOException) {
                continue
            }
        }

        return null
    }

    @Composable
    fun show() {
        val scope     = rememberCoroutineScope()
        val tile      = with(LocalDensity.current) { TILE.dp.toPx() }
        val precision = if (accuracy < 1000) "${getFixed(accuracy, 0)} m" else "${getFixed(accuracy / 1000, 0)} km"

        // endereco do ponto tocado depois de uma pausa; um toque novo cancela a busca anterior, entao nunca chega o endereco de outro ponto
        LaunchedEffect(taps) {
            if (taps == 0) return@LaunchedEffect

            delay(DELAY)
            val point = marker
            val found = withContext(Dispatchers.IO) { Api.reverse(point.first, point.second) }
            place   = found
            address = found.label
        }

        Dialog(onDismissRequest = onCancel, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
            Column(Modifier.fillMaxSize().background(COLORS.background).safeDrawingPadding().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Onde você está?", color = COLORS.text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Text(String.format(NOTES.getValue(start.third), precision) + " Arraste para mover o mapa e toque para marcar o ponto exato.", color = COLORS.muted, fontSize = 12.sp)

                if (recent.isNotEmpty()) Text("LUGARES JÁ USADOS", color = COLORS.muted, fontSize = 11.sp, fontWeight = FontWeight.Bold)

                for (item in recent) {
                    Text(item.label, color = COLORS.text, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth().background(COLORS.input, RoundedCornerShape(8.dp)).clickable { cb(item) }.padding(horizontal = 12.dp, vertical = 10.dp))
                }

                BoxWithConstraints(Modifier.fillMaxWidth().height(HEIGHT.dp).clip(RoundedCornerShape(12.dp)).background(COLORS.card)) {
                    val width  = constraints.maxWidth
                    val height = constraints.maxHeight
                    val (cx, cy) = getPixel(center.first, center.second, zoom, tile)
                    val left   = cx - width / 2.0
                    val top    = cy - height / 2.0
                    val keys   = (floor(left / tile).toInt()..floor((left + width) / tile).toInt()).flatMap { x -> (floor(top / tile).toInt()..floor((top + height) / tile).toInt()).map { y -> Triple(zoom, x, y) } }

                    LaunchedEffect(keys) {
                        for (key in keys.filter { it !in tiles && it !in pending }) {
                            pending += key

                            scope.launch {
                                val image = withContext(Dispatchers.IO) { getTile(key) }
                                pending -= key
                                if (image != null) tiles[key] = image
                            }
                        }
                    }

                    // toque marca o ponto na hora, com a coordenada como rotulo ate o endereco chegar; arraste move o mapa e a pinca troca o zoom
                    Canvas(Modifier.fillMaxSize().pointerInput(tile) {
                        detectTapGestures { offset ->
                            val (px, py) = getPixel(center.first, center.second, zoom, tile)
                            marker  = getCoords(px - size.width / 2.0 + offset.x, py - size.height / 2.0 + offset.y, zoom, tile)
                            place   = Place(String.format(Locale.US, "%.5f, %.5f", marker.first, marker.second), marker.first, marker.second)
                            address = null
                            taps++
                        }
                    }.pointerInput(tile) {
                        detectTransformGestures { _, pan, scale, _ ->
                            val (px, py) = getPixel(center.first, center.second, zoom, tile)
                            center = getCoords(px - pan.x, py - pan.y, zoom, tile)
                            pinch *= scale

                            if (pinch > PINCH || pinch < 1 / PINCH) {
                                zoom  = (zoom + if (pinch > 1) 1 else -1).coerceIn(LIMITS.first, LIMITS.last)
                                pinch = 1f
                            }
                        }
                    }) {
                        for (key in keys) {
                            val image = tiles[key] ?: continue
                            drawImage(image, dstOffset = IntOffset((key.second * tile - left).roundToInt(), (key.third * tile - top).roundToInt()), dstSize = IntSize(ceil(tile).toInt(), ceil(tile).toInt()), colorFilter = FILTER)
                        }

                        val (mx, my) = getPixel(marker.first, marker.second, zoom, tile)
                        val point = Offset((mx - left).toFloat(), (my - top).toFloat())
                        drawLine(COLORS.price, point + Offset(0f, 6.dp.toPx()), point + Offset(0f, 22.dp.toPx()), 3.dp.toPx())
                        drawCircle(COLORS.background, 9.dp.toPx(), point - Offset(0f, 3.dp.toPx()))
                        drawCircle(COLORS.price, 9.dp.toPx(), point - Offset(0f, 3.dp.toPx()), style = Stroke(3.dp.toPx()))
                    }

                    if (keys.any { it !in tiles }) Text("carregando mapa…", color = COLORS.muted, fontSize = 11.sp, modifier = Modifier.align(Alignment.TopCenter).padding(10.dp))

                    Column(Modifier.align(Alignment.TopEnd).padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        for ((text, step) in listOf("+" to 1, "−" to -1)) {
                            Button(onClick = { zoom = (zoom + step).coerceIn(LIMITS.first, LIMITS.last) }, modifier = Modifier.size(40.dp), shape = RoundedCornerShape(8.dp), contentPadding = PaddingValues(0.dp), colors = ButtonDefaults.buttonColors(containerColor = COLORS.input, contentColor = COLORS.text)) {
                                Text(text, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }

                Text(address ?: "buscando endereço…", color = if (address == null) COLORS.muted else COLORS.text, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                Text("© contribuidores do OpenStreetMap · OpenTopoMap (CC-BY-SA) · OSM France", color = COLORS.muted, fontSize = 10.sp)

                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp, Alignment.End)) {
                    OutlinedButton(onClick = onCancel, border = BorderStroke(1.dp, COLORS.border), colors = ButtonDefaults.outlinedButtonColors(contentColor = COLORS.muted)) {
                        Text("Cancelar")
                    }

                    Button(onClick = { cb(place) }, colors = ButtonDefaults.buttonColors(containerColor = COLORS.button, contentColor = Color.White)) {
                        Text("Usar este ponto", fontWeight = FontWeight.Bold)
                    }
                }
            }
        }
    }
}
