package com.klauss.tarifa

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Fill
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.math.BigDecimal
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt


// TRES LINHAS NO MESMO EIXO DE TEMPO: PRECO COM A FAIXA P10-P90, CHUVA (CHANCE POR HORA E INTENSIDADE) E TEMPO DE VIAGEM COM A FAIXA M10-M90; O TOQUE MOSTRA A CRUZETA
class Chart {
    companion object {
        val RATIOS = floatArrayOf(3f, 0.32f, 0.95f, 1.35f)                                     // preco, faixa de chance, intensidade da chuva, tempo de viagem
        val LEVELS = listOf(0.0 to "sem trânsito", 10.0 to "moderado", 30.0 to "intenso")       // % acima do tempo livre das linhas de referencia, rotuladas com o tempo de viagem de cada uma
        val STEPS  = listOf(1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0, 120.0, 180.0, 360.0)  // minutos entre marcas do eixo do tempo de viagem; vale o menor passo com ate 3 intervalos
        val CLOCK  = listOf(10, 15, 20, 30, 60, 120, 180)                                       // minutos entre rotulos do eixo de horas; vale o primeiro que der no maximo 8 intervalos
        val HHMM   = DateTimeFormatter.ofPattern("HH:mm")
        const val DRY    = 0.05     // mm/h abaixo do qual a janela inteira conta como seca
        const val CHANCE = 0.65f    // opacidade da celula com 100% de chance; acima disso o texto claro perde contraste
        const val HOURS  = 12
        const val HEIGHT = 560      // dp do grafico
        const val LEFT   = 50       // dp reservados aos rotulos do eixo y
        const val RIGHT  = 8
        const val TITLE  = 20       // dp da linha de titulo de cada painel
        const val GAP    = 10
        const val AXIS   = 20       // dp dos rotulos de hora sob o transito
    }

    var hours by mutableIntStateOf(5)
    var index by mutableStateOf<Int?>(null)

    @Composable
    fun show(tick: Tick) {
        val series   = tick.series
        val n        = series.ts.count { it <= series.ts[0] + hours * 3600 }
        val instant  = Instant.ofEpochSecond(series.ts[0])
        val offset   = ZoneId.of(tick.route.tz).rules.getOffset(instant)
        val note     = if (offset != ZoneId.systemDefault().rules.getOffset(instant)) " · horário de ${tick.route.tz.substringAfterLast('/').replace('_', ' ')} (UTC${getSigned(offset.totalSeconds / 3600.0, 0)})" else ""
        val measurer = rememberTextMeasurer()

        Column(Modifier.fillMaxWidth().background(COLORS.card, RoundedCornerShape(16.dp)).padding(horizontal = 12.dp, vertical = 14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Próxima${if (hours > 1) "s" else ""} $hours h · passos de 10 min$note", color = COLORS.text, fontSize = 14.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text("janela $hours h", color = COLORS.secondary, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            }

            Slider(value = hours.toFloat(), onValueChange = { hours = it.roundToInt(); index = null }, valueRange = 1f..HOURS.toFloat(), steps = HOURS - 2, colors = SliderDefaults.colors(thumbColor = COLORS.button, activeTrackColor = COLORS.button, inactiveTrackColor = COLORS.input, activeTickColor = COLORS.button, inactiveTickColor = COLORS.border))

            Canvas(Modifier.fillMaxWidth().height(HEIGHT.dp).pointerInput(n, series) {
                detectTapGestures { offset -> getIndex(offset.x, LEFT.dp.toPx(), size.width - RIGHT.dp.toPx(), series.ts, n).let { index = if (it == index) null else it } }
            }.pointerInput(n, series) {
                detectHorizontalDragGestures { change, _ -> index = getIndex(change.position.x, LEFT.dp.toPx(), size.width - RIGHT.dp.toPx(), series.ts, n) }
            }) {
                plot(measurer, tick, n)
            }
        }
    }

    // SLOT MAIS PERTO DO TOQUE NA LARGURA UTIL DO GRAFICO
    fun getIndex(x: Float, left: Float, right: Float, ts: LongArray, n: Int): Int {
        val t = ts[0] + ((x - left) / (right - left)).coerceIn(0f, 1f) * (ts[n - 1] - ts[0])
        return (0 until n).minBy { abs(ts[it] - t) }
    }

    // MARCAS REDONDAS DO EIXO (1, 2, 2,5 OU 5 VEZES UMA POTENCIA DE 10) COM NO MAXIMO BINS INTERVALOS, COMO O MAXNLOCATOR DO MATPLOTLIB
    fun getTicks(low: Double, high: Double, bins: Int): List<Double> {
        val raw  = (high - low) / bins
        val base = 10.0.pow(floor(log10(raw)))
        val step = listOf(1.0, 2.0, 2.5, 5.0, 10.0).map { it * base }.first { it >= raw * (1 - 1e-9) }
        return generateSequence(ceil(low / step - 1e-9) * step) { it + step }.takeWhile { it <= high + step * 1e-9 }.map { getRound(it, 6) + 0.0 }.toList()    // + 0.0 tira o -0 que viraria "-0%"
    }

    // TEXTO ALINHADO PELO PONTO; O HALO NA COR DO FUNDO MANTEM O ROTULO LEGIVEL QUANDO UMA CURVA PASSA POR BAIXO DELE
    fun DrawScope.showText(measurer: TextMeasurer, text: String, x: Float, y: Float, color: Color, size: TextUnit, align: Float = 0f, valign: Float = 0f, bold: Boolean = false, halo: Color? = null) {
        val layout = measurer.measure(text, TextStyle(color = color, fontSize = size, fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal))
        val corner = Offset(x - layout.size.width * align, y - layout.size.height * valign)
        if (halo != null) drawText(layout, color = halo, topLeft = corner, drawStyle = Stroke(3.dp.toPx(), join = StrokeJoin.Round))
        drawText(layout, topLeft = corner, drawStyle = Fill)    // o paint do layout guarda o ultimo estilo: sem Fill explicito o texto sai contornado
    }

    fun DrawScope.plot(measurer: TextMeasurer, tick: Tick, n: Int) {
        val route   = tick.route
        val series  = tick.series
        val ts      = series.ts
        val left    = LEFT.dp.toPx()
        val right   = size.width - RIGHT.dp.toPx()
        val title   = TITLE.dp.toPx()
        val gap     = GAP.dp.toPx()
        val usable  = size.height - 3 * title - 2.5f * gap - AXIS.dp.toPx()
        val heights = RATIOS.map { usable * it / RATIOS.sum() }
        val tops    = listOf(title, title + heights[0] + gap + title).let { it + (it[1] + heights[1] + gap / 2) }.let { it + (it[2] + heights[2] + gap + title) }
        val p10     = series.bands.getValue("p10")
        val p50     = series.bands.getValue("p50")
        val p90     = series.bands.getValue("p90")
        val m50     = series.bands.getValue("m50")
        val free    = route.duration
        val xAt     = { t: Long -> left + (t - ts[0]).toFloat() / (ts[n - 1] - ts[0]) * (right - left) }
        val clock   = { t: Long -> getLocal(t, route.tz).format(HHMM) }

        // sem legenda: o titulo de cada painel diz o que ele mostra e os rotulos ficam direto nas marcas
        for ((top, text, detail) in listOf(Triple(tops[0], "Preço da corrida", "previsto (p50) e faixa p10–p90"), Triple(tops[1], "Chuva prevista", "chance por hora e intensidade"), Triple(tops[3], "Tempo de viagem previsto", "previsto (m50) e faixa m10–m90"))) {
            showText(measurer, text, left, top - 4.dp.toPx(), COLORS.text, 12.sp, valign = 1f, bold = true)
            showText(measurer, detail, right, top - 4.dp.toPx(), COLORS.muted, 10.sp, align = 1f, valign = 1f)
        }

        val low   = (0 until n).minOf { p10[it] }
        val high  = (0 until n).maxOf { p90[it] }
        val pad   = max(high - low, 2.0)
        val floor = low - 0.10 * pad
        val roof  = high + 0.25 * pad
        val yp    = { v: Double -> (tops[0] + (1 - (v - floor) / (roof - floor)) * heights[0]).toFloat() }

        for (t in getTicks(floor, roof, 4)) {
            drawLine(COLORS.grid, Offset(left, yp(t)), Offset(right, yp(t)), 1.dp.toPx())
            showText(measurer, "R$ ${getFixed(t, 0)}", left - 6.dp.toPx(), yp(t), COLORS.muted, 10.sp, align = 1f, valign = 0.5f)
        }

        drawPath(Path().apply { moveTo(xAt(ts[0]), yp(p90[0])); for (i in 1 until n) lineTo(xAt(ts[i]), yp(p90[i])); for (i in n - 1 downTo 0) lineTo(xAt(ts[i]), yp(p10[i])); close() }, COLORS.price.copy(alpha = 0.18f))
        drawPath(Path().apply { moveTo(xAt(ts[0]), yp(p50[0])); for (i in 1 until n) lineTo(xAt(ts[i]), yp(p50[i])) }, COLORS.price, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        drawCircle(COLORS.card, 7.dp.toPx(), Offset(xAt(ts[0]), yp(series.price)))
        drawCircle(COLORS.text, 5.dp.toPx(), Offset(xAt(ts[0]), yp(series.price)))
        showText(measurer, "agora", xAt(ts[0]) + 5.dp.toPx(), tops[0] + heights[0] - 4.dp.toPx(), COLORS.muted, 10.sp, valign = 1f)

        val peak = (0 until n).maxBy { p50[it] }
        drawCircle(COLORS.card, 7.dp.toPx(), Offset(xAt(ts[peak]), yp(p50[peak])))
        drawCircle(COLORS.price, 5.dp.toPx(), Offset(xAt(ts[peak]), yp(p50[peak])))
        showText(measurer, "pico ${getMoney(p50[peak])} às ${clock(ts[peak])}", xAt(ts[peak]), yp(p90[peak]) - 8.dp.toPx(), COLORS.text, 11.sp, align = if (peak < n / 4f) 0f else if (peak > n * 3 / 4f) 1f else 0.5f, valign = 1f)

        // a chance vem de hora em hora: a cor de cada slot mostra a transicao e o numero fica no meio do trecho visivel de cada hora
        drawRect(COLORS.input, Offset(left, tops[1]), Size(right - left, heights[1]))

        for (i in 0 until n - 1) {
            val odds = series.probability[i].takeIf { it.isFinite() } ?: 0.0
            drawRect(COLORS.rain.copy(alpha = CHANCE * (odds / 100).toFloat().coerceIn(0f, 1f)), Offset(xAt(ts[i]), tops[1]), Size(xAt(ts[i + 1]) - xAt(ts[i]), heights[1]))
        }

        for (group in (0 until n).groupBy { getLocal(ts[it], route.tz).truncatedTo(ChronoUnit.HOURS) }.values) {
            val odds = group.map { series.probability[it] }.filter { it.isFinite() }

            // ao menos 40 min visiveis da hora para o rotulo caber sem encostar na borda; a janela de 1 h sempre tem uma assim
            if (group.size >= 4 && odds.isNotEmpty()) showText(measurer, "${getFixed(odds.average(), 0)}%", xAt((ts[group.first()] + min(ts[group.last()] + STEP, ts[n - 1])) / 2), tops[1] + heights[1] / 2, COLORS.text, 10.sp, align = 0.5f, valign = 0.5f)
        }

        showText(measurer, "chance", left - 6.dp.toPx(), tops[1] + heights[1] / 2, COLORS.muted, 10.sp, align = 1f, valign = 0.5f)

        val rain = (0 until n).maxOf { series.rain[it] }
        val wet  = max(rain * 1.3, 2.0)
        val yr   = { v: Double -> (tops[2] + (1 - v / wet) * heights[2]).toFloat() }

        for (t in getTicks(0.0, wet, 2)) {
            drawLine(COLORS.grid, Offset(left, yr(t)), Offset(right, yr(t)), 1.dp.toPx())
            showText(measurer, "${BigDecimal.valueOf(t).stripTrailingZeros().toPlainString().replace('.', ',')} mm/h", left - 6.dp.toPx(), yr(t), COLORS.muted, 10.sp, align = 1f, valign = 0.5f)
        }

        drawPath(Path().apply { moveTo(xAt(ts[0]), yr(0.0)); for (i in 0 until n) lineTo(xAt(ts[i]), yr(series.rain[i])); lineTo(xAt(ts[n - 1]), yr(0.0)); close() }, COLORS.rain.copy(alpha = 0.30f))
        drawPath(Path().apply { moveTo(xAt(ts[0]), yr(series.rain[0])); for (i in 1 until n) lineTo(xAt(ts[i]), yr(series.rain[i])) }, COLORS.rain, style = Stroke(1.8f.dp.toPx(), join = StrokeJoin.Round))

        if (rain < DRY) showText(measurer, "sem chuva prevista na janela", (left + right) / 2, tops[2] + heights[2] * 0.45f, COLORS.muted, 10.sp, align = 0.5f, valign = 0.5f)

        // o tempo de viagem em minutos (ou horas) com a mesma escala do preco: faixa, mediana e linhas de referencia com o tempo de cada nivel de transito
        val m10    = series.bands.getValue("m10")
        val m90    = series.bands.getValue("m90")
        val fast   = min((0 until n).minOf { m10[it] }, free)
        val slow   = (0 until n).maxOf { m90[it] }
        val span   = max(slow - fast, 2.0)
        val bottom = fast - 0.10 * span
        val top    = slow + 0.25 * span
        val yt     = { v: Double -> (tops[3] + (1 - (v - bottom) / (top - bottom)) * heights[3]).toFloat() }
        val step   = STEPS.firstOrNull { (top - bottom) / it <= 3 } ?: STEPS.last()

        for (t in generateSequence(ceil(bottom / step - 1e-9) * step) { it + step }.takeWhile { it <= top + 1e-9 }) {
            drawLine(COLORS.grid, Offset(left, yt(t)), Offset(right, yt(t)), 1.dp.toPx())
            showText(measurer, getDuration(t), left - 6.dp.toPx(), yt(t), COLORS.muted, 10.sp, align = 1f, valign = 0.5f)
        }

        drawPath(Path().apply { moveTo(xAt(ts[0]), yt(m90[0])); for (i in 1 until n) lineTo(xAt(ts[i]), yt(m90[i])); for (i in n - 1 downTo 0) lineTo(xAt(ts[i]), yt(m10[i])); close() }, COLORS.traffic.copy(alpha = 0.18f))
        drawPath(Path().apply { moveTo(xAt(ts[0]), yt(m50[0])); for (i in 1 until n) lineTo(xAt(ts[i]), yt(m50[i])) }, COLORS.traffic, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))

        for ((level, name) in LEVELS) {
            val minutes = free * (1 + level / 100)
            if (minutes > top) continue
            drawLine(COLORS.border, Offset(left, yt(minutes)), Offset(right, yt(minutes)), 1.dp.toPx())
            showText(measurer, "$name · ${getDuration(minutes)}", right - 4.dp.toPx(), yt(minutes) - 3.dp.toPx(), COLORS.muted, 10.sp, align = 1f, valign = 1f, halo = COLORS.card)
        }

        // o eixo mostra a hora do lugar da rota, em passos redondos do relogio local
        val every = CLOCK.firstOrNull { (ts[n - 1] - ts[0]) / 60.0 / it <= 8 } ?: CLOCK.last()
        val shift = ZoneId.of(route.tz).rules.getOffset(Instant.ofEpochSecond(ts[0])).totalSeconds
        var mark  = ((ts[0] + shift) / (every * 60) + 1) * every * 60 - shift

        while (mark <= ts[n - 1]) {
            val x = xAt(mark)
            showText(measurer, clock(mark), x, tops[3] + heights[3] + 4.dp.toPx(), COLORS.muted, 10.sp, align = if (x < left + 16.dp.toPx()) 0f else if (x > right - 16.dp.toPx()) 1f else 0.5f)
            mark += every * 60
        }

        val i = index?.takeIf { it < n } ?: return

        for (k in 0 until 4) drawLine(COLORS.muted, Offset(xAt(ts[i]), tops[k]), Offset(xAt(ts[i]), tops[k] + heights[k]), 1.dp.toPx())

        val seen    = if (i == 0) "\n${getMoney(series.price)} e ${getDuration(series.minutes)}   observados" else ""
        val arrival = getLocal((ts[i] + m50[i] * 60).toLong(), route.tz).format(HHMM)
        val text    = "${clock(ts[i])}$seen\n${getMoney(p50[i])}   previsto (${getMoney(p10[i])} a ${getMoney(p90[i])})\n${getDuration(m50[i])}   viagem (${getSpan(m10[i], m90[i])})\nchegada às $arrival   ${getDelay(m50[i] - free)} de trânsito\n${getFixed(series.rain[i], 1)} mm/h · ${getFixed(series.probability[i], 0)}% de chance   chuva"
        val layout = measurer.measure(text, TextStyle(color = COLORS.text, fontSize = 11.sp, lineHeight = 16.sp))
        val inset  = 8.dp.toPx()
        val box    = Size(layout.size.width + 2 * inset, layout.size.height + 2 * inset)
        val corner = Offset((if (i > n / 2) xAt(ts[i]) - 12.dp.toPx() - box.width else xAt(ts[i]) + 12.dp.toPx()).coerceIn(0f, max(0f, size.width - box.width)), (yp(p50[i]) - box.height / 2).coerceIn(0f, max(0f, size.height - box.height)))
        drawRoundRect(COLORS.input, corner, box, CornerRadius(8.dp.toPx()))
        drawRoundRect(COLORS.border, corner, box, CornerRadius(8.dp.toPx()), style = Stroke(1.dp.toPx()))
        drawText(layout, topLeft = corner + Offset(inset, inset))
    }
}
