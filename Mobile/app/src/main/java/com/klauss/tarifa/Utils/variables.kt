package com.klauss.tarifa


const val STEP    = 600L          // resolucao da serie e do oraculo (10 min)
const val HORIZON = 12 * 3600L    // a serie e sempre calculada inteira; a janela da tela so recorta o que aparece

// aplicativo -> nome na tela; a ordem e o indice que o modelo usa como categoria e e a mesma do desktop
val COMPANIES = linkedMapOf("uber" to "Uber", "99" to "99")
