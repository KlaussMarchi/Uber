package com.klauss.tarifa

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.min
import kotlin.math.sin


const val RATE   = 44100
const val VOLUME = 0.5

// (frequencia Hz, duracao s): arpejo ascendente de do maior quando o preco cai, dois graves descendentes quando sobe
val NOTES = mapOf(
    "good" to listOf(1046.5 to 0.11, 1318.5 to 0.11, 1568.0 to 0.20),
    "bad"  to listOf(440.0 to 0.20, 311.1 to 0.34),
)

// PCM MONO 16 BITS DO ALERTA, O MESMO DO DESKTOP: SENOIDE COM TERCEIRO HARMONICO, ATAQUE DE 5 MS E DECAIMENTO PARA NAO ESTALAR ENTRE NOTAS
fun getTone(kind: String) = NOTES.getValue(kind).flatMap { (f, duration) ->
    List((RATE * duration).toInt()) { i ->
        val t = i.toDouble() / RATE
        ((sin(2 * PI * f * t) + 0.3 * sin(6 * PI * f * t)) * min(t / 0.005, 1.0) * exp(-t / (duration / 2.5)) / 1.3 * VOLUME * 32767).toInt().toShort()
    }
}.toShortArray()

// TOCA O ALERTA COMO SOM DE NOTIFICACAO, QUE RESPEITA O MODO SILENCIOSO DO CELULAR, E LIBERA O AUDIO QUANDO ACABA
fun play(kind: String) {
    val samples = getTone(kind)
    val track   = AudioTrack.Builder()
        .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
        .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
        .setBufferSizeInBytes(samples.size * 2)
        .setTransferMode(AudioTrack.MODE_STATIC)
        .build()

    track.write(samples, 0, samples.size)
    track.play()
    Handler(Looper.getMainLooper()).postDelayed(track::release, samples.size * 1000L / RATE + 500)
}
