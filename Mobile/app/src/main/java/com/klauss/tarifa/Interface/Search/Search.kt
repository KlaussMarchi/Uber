package com.klauss.tarifa

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext


// CAMPO DE ENDERECO COM AUTOCOMPLETE E BOTAO DE LOCALIZACAO; SO CONTA COMO VALIDADO QUANDO UMA SUGESTAO OU UM PONTO DO MAPA E ESCOLHIDO
class Search(val title: String, val placeholder: String) {
    companion object {
        const val DELAY     = 350L    // ms de pausa na digitacao antes de consultar
        const val MIN_CHARS = 3
        const val LIMIT     = 6
    }

    var text     by mutableStateOf("")
    var selected by mutableStateOf<Place?>(null)
    var results  by mutableStateOf(emptyList<Place>())
    var notice   by mutableStateOf<String?>(null)    // aviso no lugar das sugestoes: buscando, sem conexao ou nada encontrado
    var locating by mutableStateOf(false)
    var job: Job? = null

    fun get() = selected ?: Place(text.trim())

    fun set(place: Place) {
        job?.cancel()
        text     = place.label
        selected = place
        hide()
    }

    // DIGITACAO: SO QUANDO O TEXTO MUDA INVALIDA A ESCOLHA ANTERIOR E AGENDA A BUSCA PARA A PAUSA DO USUARIO; RESPOSTA DE BUSCA ANTIGA E DESCARTADA
    fun handleKey(value: String, scope: CoroutineScope) {
        val query = value.trim()
        val same  = query == text.trim()
        text = value

        if (same) return

        selected = null
        job?.cancel()

        if (query.length < MIN_CHARS) return hide()

        job = scope.launch {
            delay(DELAY)
            results = emptyList()
            notice  = "Buscando endereços…"
            val places = withContext(Dispatchers.IO) { Api.search(query, LIMIT) }

            if (query != text.trim() || selected != null) return@launch

            results = places.orEmpty()
            notice  = if (places == null) "Sem conexão com o serviço de endereços" else if (places.isEmpty()) "Nenhum endereço encontrado" else null
        }
    }

    // FECHA AS SUGESTOES SEM MEXER NO CAMPO
    fun hide() {
        results = emptyList()
        notice  = null
    }

    @Composable
    fun show(onAction: () -> Unit, onLocate: () -> Unit) {
        val scope  = rememberCoroutineScope()
        val focus  = LocalFocusManager.current
        val border = if (selected != null) COLORS.success else COLORS.border

        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, color = COLORS.muted, fontSize = 12.sp, fontWeight = FontWeight.Bold)

            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value           = text,
                    onValueChange   = { handleKey(it, scope) },
                    modifier        = Modifier.weight(1f),
                    singleLine      = true,
                    placeholder     = { Text(placeholder, color = COLORS.muted, fontSize = 15.sp) },
                    textStyle       = TextStyle(color = COLORS.text, fontSize = 15.sp),
                    shape           = RoundedCornerShape(10.dp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { hide(); focus.clearFocus(); onAction() }),
                    colors          = OutlinedTextFieldDefaults.colors(focusedContainerColor = COLORS.input, unfocusedContainerColor = COLORS.input, focusedBorderColor = if (selected != null) COLORS.success else COLORS.button, unfocusedBorderColor = border, cursorColor = COLORS.text, focusedTextColor = COLORS.text, unfocusedTextColor = COLORS.text),
                )

                OutlinedButton(onClick = onLocate, enabled = !locating, modifier = Modifier.size(56.dp), shape = RoundedCornerShape(10.dp), contentPadding = PaddingValues(0.dp), border = BorderStroke(1.dp, COLORS.border), colors = ButtonDefaults.outlinedButtonColors(containerColor = COLORS.input, contentColor = COLORS.text, disabledContainerColor = COLORS.input)) {
                    if (locating) CircularProgressIndicator(Modifier.size(20.dp), color = COLORS.muted, strokeWidth = 2.dp) else Text("◎", fontSize = 22.sp)
                }
            }

            if (notice != null || results.isNotEmpty()) {
                Column(Modifier.fillMaxWidth().background(COLORS.input, RoundedCornerShape(10.dp)).border(1.dp, COLORS.border, RoundedCornerShape(10.dp)).padding(4.dp)) {
                    notice?.let { Text(it, color = COLORS.muted, fontSize = 13.sp, modifier = Modifier.padding(10.dp)) }

                    for (place in results) {
                        Text(place.label, color = COLORS.text, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth().clickable { set(place); focus.clearFocus() }.padding(horizontal = 10.dp, vertical = 12.dp))
                    }
                }
            }
        }
    }
}
