package com.klauss.tarifa

import kotlin.math.exp
import kotlin.math.ln1p


// GERADOR DO NUMPY (DEFAULT_RNG) BIT A BIT: SEEDSEQUENCE -> PCG64 XSL-RR -> NORMAL ZIGGURAT E POISSON, PARA O ORACULO SIMULAR O MESMO MUNDO DO DESKTOP
class Rng(vararg entropy: Long) {
    companion object {
        const val INIT_A     = 0x43b0d7e5u
        const val MULT_A     = 0x931e8875u
        const val INIT_B     = 0x8b51f9ddu
        const val MULT_B     = 0x58f38dedu
        const val MIX_MULT_L = 0xca01f9ddu
        const val MIX_MULT_R = 0x4973f715u
        const val PCG_DEFAULT_MULTIPLIER_HIGH = 2549297995355413924uL
        const val PCG_DEFAULT_MULTIPLIER_LOW  = 4865540595714422341uL
    }

    var hi    = 0uL
    var lo    = 0uL
    var incHi = 0uL
    var incLo = 0uL

    init {
        val words = entropy.flatMap { n -> if (n == 0L) listOf(0u) else generateSequence(n.toULong()) { it shr 32 }.takeWhile { it != 0uL }.map { it.toUInt() }.toList() }
        var hash  = INIT_A

        fun hashmix(value: UInt): UInt {
            val v = value xor hash
            hash *= MULT_A
            val m = v * hash
            return m xor (m shr 16)
        }

        fun mix(x: UInt, y: UInt): UInt {
            val m = MIX_MULT_L * x - MIX_MULT_R * y
            return m xor (m shr 16)
        }

        val pool = Array(4) { hashmix(words.getOrElse(it) { 0u }) }

        for (src in 0 until 4) for (dst in 0 until 4) if (src != dst) pool[dst] = mix(pool[dst], hashmix(pool[src]))
        for (src in 4 until words.size) for (dst in 0 until 4) pool[dst] = mix(pool[dst], hashmix(words[src]))

        var hashB = INIT_B
        val state = Array(8) { i -> val v = pool[i % 4] xor hashB; hashB *= MULT_B; val m = v * hashB; m xor (m shr 16) }
        val seed  = Array(4) { i -> state[2 * i].toULong() or (state[2 * i + 1].toULong() shl 32) }

        incHi = (seed[2] shl 1) or (seed[3] shr 63)
        incLo = (seed[3] shl 1) or 1uL
        step()
        val sum = lo + seed[1]
        hi = hi + seed[0] + (if (sum < lo) 1uL else 0uL)
        lo = sum
        step()
    }

    // ESTADO DE 128 BITS VEZES O MULTIPLICADOR MAIS O INCREMENTO, EM DUAS PALAVRAS DE 64 BITS
    fun step() {
        val a = lo and 0xFFFFFFFFuL
        val b = lo shr 32
        val c = PCG_DEFAULT_MULTIPLIER_LOW and 0xFFFFFFFFuL
        val d = PCG_DEFAULT_MULTIPLIER_LOW shr 32
        val cross = ((a * c) shr 32) + ((b * c) and 0xFFFFFFFFuL) + a * d
        val high  = b * d + ((b * c) shr 32) + (cross shr 32) + lo * PCG_DEFAULT_MULTIPLIER_HIGH + hi * PCG_DEFAULT_MULTIPLIER_LOW
        val low   = lo * PCG_DEFAULT_MULTIPLIER_LOW
        lo = low + incLo
        hi = high + incHi + (if (lo < low) 1uL else 0uL)
    }

    fun next(): ULong {
        step()
        return (hi xor lo).rotateRight((hi shr 58).toInt())
    }

    fun getDouble() = (next() shr 11).toDouble() * (1.0 / 9007199254740992.0)

    // NORMAL PADRAO PELO ZIGGURAT DO NUMPY (RANDOM_STANDARD_NORMAL), COM AS MESMAS TABELAS E A MESMA ORDEM DE SORTEIOS
    fun getNormal(): Double {
        while (true) {
            val r    = next()
            val idx  = (r and 0xffuL).toInt()
            val rest = r shr 8
            val rabs = ((rest shr 1) and 0x000fffffffffffffuL).toLong()
            val x    = if (rest and 1uL != 0uL) -(rabs * wi_double[idx]) else rabs * wi_double[idx]

            if (rabs < ki_double[idx]) return x

            if (idx == 0) {
                while (true) {
                    val xx = -ziggurat_nor_inv_r * ln1p(-getDouble())
                    val yy = -ln1p(-getDouble())
                    if (yy + yy > xx * xx) return if ((rabs shr 8) and 1L != 0L) -(ziggurat_nor_r + xx) else ziggurat_nor_r + xx
                }
            }

            if ((fi_double[idx - 1] - fi_double[idx]) * getDouble() + fi_double[idx] < exp(-0.5 * x * x)) return x
        }
    }

    // POISSON POR MULTIPLICACAO DE UNIFORMES, O RAMO QUE O NUMPY USA PARA LAMBDA MENOR QUE 10
    fun getPoisson(lam: Double): Int {
        val limit = exp(-lam)
        var count = 0
        var prod  = 1.0

        while (true) {
            prod *= getDouble()
            if (prod <= limit) return count
            count++
        }
    }
}
