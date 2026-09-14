package com.klauss.tarifa

import java.math.BigDecimal
import java.math.RoundingMode
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.abs
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt


val holidays = ConcurrentHashMap<Int, Set<LocalDate>>()

// DIA DA SEMANA (0 = SEGUNDA, FERIADO VALE COMO DOMINGO), HORA LOCAL CONTINUA E DIA LOCAL (DIAS DESDE 1970) DE CADA INSTANTE
class Clock(val weekday: IntArray, val hour: DoubleArray, val day: LongArray)

// FERIADOS NACIONAIS DO CALENDARIO ANBIMA: OS 10 DE LEI MAIS CARNAVAL E CORPUS CHRISTI; PASCOA PELO ALGORITMO DE MEEUS/JONES/BUTCHER
fun getHolidays(year: Int) = holidays.getOrPut(year) {
    val a = year % 19; val b = year / 100; val c = year % 100
    val d = b / 4; val e = b % 4
    val g = (b - (b + 8) / 25 + 1) / 3
    val h = (19 * a + b - d - g + 15) % 30
    val l = (32 + 2 * e + 2 * (c / 4) - h - c % 4) % 7
    val m = (a + 11 * h + 22 * l) / 451
    val easter = LocalDate.of(year, (h + l - 7 * m + 114) / 31, (h + l - 7 * m + 114) % 31 + 1)
    val fixed  = listOf(1 to 1, 4 to 21, 5 to 1, 9 to 7, 10 to 12, 11 to 2, 11 to 15, 11 to 20, 12 to 25)
    val moving = listOf(-48L, -47L, -2L, 60L)    // segunda e terca de carnaval, sexta-feira santa, corpus christi
    fixed.map { (month, day) -> LocalDate.of(year, month, day) }.toSet() + moving.map { easter.plusDays(it) }
}

// RELOGIO DA DEMANDA NO FUSO DA ROTA; PRECO E TRANSITO SEGUEM O LUGAR DA CORRIDA, NAO O CELULAR
fun getClock(ts: LongArray, tz: String): Clock {
    val zone  = ZoneId.of(tz)
    val clock = Clock(IntArray(ts.size), DoubleArray(ts.size), LongArray(ts.size))

    for (i in ts.indices) {
        val local = Instant.ofEpochSecond(ts[i]).atZone(zone)
        val date  = local.toLocalDate()
        clock.weekday[i] = if (date in getHolidays(date.year)) 6 else date.dayOfWeek.value - 1
        clock.hour[i]    = local.hour + local.minute / 60.0
        clock.day[i]     = date.toEpochDay()
    }

    return clock
}

// INICIO DO DIA LOCAL EM UNIX TIME; NUM DIA QUE PULA A MEIA-NOITE (HORARIO DE VERAO) VALE O PRIMEIRO INSTANTE QUE EXISTE
fun getMidnight(day: Long, tz: String) = LocalDate.ofEpochDay(day).atStartOfDay(ZoneId.of(tz)).toEpochSecond()

fun getLocal(ts: Long, tz: String): ZonedDateTime = Instant.ofEpochSecond(ts).atZone(ZoneId.of(tz))

// DISTANCIA EM LINHA RETA ENTRE DOIS PONTOS (HAVERSINE), EM KM
fun getDistance(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
    val p1 = Math.toRadians(lat1); val p2 = Math.toRadians(lat2)
    val dp = p2 - p1; val dl = Math.toRadians(lon2) - Math.toRadians(lon1)
    return 2 * 6371.0088 * asin(sqrt(sin(dp / 2).pow(2) + cos(p1) * cos(p2) * sin(dl / 2).pow(2)))
}

// INTERPOLACAO LINEAR DO NP.INTERP: CONSTANTE FORA DA GRADE E VALOR EXATO SOBRE UM PONTO DELA
fun getInterp(x: Double, xp: DoubleArray, fp: DoubleArray): Double {
    if (x <= xp[0]) return fp[0]
    if (x >= xp.last()) return fp.last()
    var j = xp.binarySearch(x)
    if (j >= 0) return fp[j]
    j = -j - 2
    return (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]) * (x - xp[j]) + fp[j]
}

// ARREDONDAMENTO DO NP.ROUND: MEIO PARA O PAR SOBRE O VALOR ESCALADO
fun getRound(x: Double, digits: Int): Double {
    val scale = 10.0.pow(digits)
    return Math.rint(x * scale) / scale
}

// NUMERO COM CASAS FIXAS E VIRGULA, ARREDONDADO COMO O FORMAT DO PYTHON: MEIO PARA O PAR SOBRE O VALOR BINARIO EXATO
fun getFixed(value: Double, digits: Int) = if (value.isFinite()) BigDecimal(value).setScale(digits, RoundingMode.HALF_EVEN).toPlainString().replace('.', ',') else "—"

// NUMERO COM SINAL SEMPRE VISIVEL, COMO O "+.0F" DO PYTHON
fun getSigned(value: Double, digits: Int) = (if (value.toRawBits() < 0) "-" else "+") + getFixed(abs(value), digits)

// VALOR EM REAIS NO PADRAO BRASILEIRO (R$ 1.234,56)
fun getMoney(value: Double) = if (value.isFinite()) "R$ " + DecimalFormat("#,##0.00", DecimalFormatSymbols(Locale.US)).format(BigDecimal(value).setScale(2, RoundingMode.HALF_EVEN)).replace(',', '_').replace('.', ',').replace('_', '.') else "—"

// DURACAO LEGIVEL ARREDONDADA AO MINUTO COMO O ROUND DO PYTHON: 47 min ATE UMA HORA, DEPOIS 2h05
fun getDuration(minutes: Double): String {
    val total = Math.rint(minutes).toLong()
    return if (total < 60) "$total min" else "${total / 60}h${(total % 60).toString().padStart(2, '0')}"
}

// FAIXA DE DURACAO SEM REPETIR A UNIDADE QUANDO AS DUAS PONTAS FICAM ABAIXO DE UMA HORA (40 a 45 min, 55 min a 1h05, 1h05 a 1h20)
fun getSpan(low: Double, high: Double) = if (Math.rint(high) < 60) "${Math.rint(low).toLong()} a ${getDuration(high)}" else "${getDuration(low)} a ${getDuration(high)}"

// DIFERENCA DE TEMPO COM SINAL (+14 min, -2 min, +1h05), NO FORMATO DA DURACAO
fun getDelay(minutes: Double): String {
    val total = Math.rint(minutes)
    return (if (total < 0) "-" else "+") + getDuration(abs(total))
}
