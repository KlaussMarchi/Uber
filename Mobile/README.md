# Tarifa Dinâmica — Android

App Android que faz o mesmo que o app de desktop (`../Desktop`): seletor **Uber | 99**, preço e tempo de viagem
agora e a previsão das próximas 12 h em passos de 10 min (p10, p50, p90), com chuva prevista e tempo de viagem
previsto no mesmo eixo de tempo, acompanhamento em tempo real a cada 30 s, alerta sonoro a cada 10% de afastamento
do preço da primeira consulta e calibração da tarifa pelo preço real do aplicativo.

> **O preço vem de dados reais**, como no desktop: não existe API pública gratuita de preços da Uber ou da 99, então
> o app usa as **médias reais que a própria Uber publica por trecho** (699 trechos, 219 municípios, 19 estados, coletados
> em 26/09/2026) para estimar a tarifa sem dinâmica, o nível de preço de cada município de origem e o tempo real de
> viagem; a 99 sai da razão típica entre as duas. O campo *preço real no app* ajusta a estimativa ao que o aplicativo
> cobra agora. Endereços (OpenStreetMap via Photon e Nominatim), rotas (OSRM), clima (Open-Meteo) e localização (GPS do
> celular, ou Wi-Fi e IP) são dados reais.

## Instalar

1. Copie `TarifaDinamica.apk` (1,3 MB) para o celular.
2. Abra o arquivo e permita "instalar apps desconhecidos" para o gerenciador de arquivos quando o Android pedir.
3. Android 8.0 ou mais novo. O app pede localização (só ao tocar em ◎) e notificações (para o tempo real e os alertas).

## Como usar

1. Escolha o aplicativo no seletor **Uber | 99** e digite origem e destino, escolhendo uma sugestão (a borda fica
   verde quando o endereço está validado). Trocar de aplicativo recalcula na hora, sem consultar a rede de novo.
2. O ◎ lê o GPS e abre o mapa "Onde você está?" na posição: toque no ponto exato ou escolha um lugar já usado.
   Sem permissão ou sem sinal de GPS, usa a estimativa por IP e, como ela erra quilômetros, abre no lugar já usado
   mais recente dentro do erro; sem nenhuma estimativa, no último lugar usado ou na região. Arraste para mover o
   mapa e use a pinça (ou +/−) para o zoom — o mesmo critério do desktop.
3. **Calcular preço** mostra o preço agora, a variação desde a primeira consulta, a faixa dos próximos 10 min, o
   resumo da rota e o gráfico: preço com a faixa p10–p90, chuva prevista e **tempo de viagem previsto** em minutos
   (ou horas), com as linhas sem trânsito, moderado e intenso. Arraste o dedo no gráfico para a cruzeta, que mostra
   também a hora de chegada; a barra escolhe a janela de 1 a 12 h.
4. **Informar o preço real.** Abra a Uber ou a 99 na mesma rota, veja quanto o app cobra agora, digite o valor em
   *preço real no app* e toque em **Calibrar**: o preço de agora passa a ser exatamente esse, a dinâmica que ele
   revela se dissipa nas horas seguintes e a parte que persiste ajusta o nível da região; a nota sob o seletor diz
   de onde vem o preço e quantos preços seus por perto o ajustam. **0** apaga os seus preços daquele aplicativo. Os
   preços informados ficam no celular e o status mostra, com o tempo, quanto as previsões feitas antes erraram.
5. O **tempo real** continua com o app fechado: uma notificação fixa mostra o preço e a variação e tem o botão
   **Parar**. A cada 10% de queda toca o arpejo ascendente e a cada 10% de alta o grave descendente (os mesmos sons do
   desktop), junto com uma notificação. O acompanhamento para sozinho 3 h depois da última consulta, para poupar
   bateria, e volta ao reabrir o app.

## Por que Kotlin nativo

- **Python direto para APK não serve para este código.** O LightGBM não tem pacote para Android (Chaquopy responde
  404 e o python-for-android não tem receita), o Chaquopy trava o pandas em 2.1.3 com Python 3.9, e o CustomTkinter
  não existe no Android — a interface teria de ser refeita de qualquer jeito, num APK de ~100 MB.
- **React Native** exigiria NDK, módulos de terceiros para serviço em primeiro plano e som gerado, mapa sem chave
  do Google e a aritmética de 128 bits do gerador do numpy em BigInt.
- **Kotlin com Jetpack Compose** tem serviço em primeiro plano, GPS, notificações, SQLite e síntese de som nativos,
  sem nenhuma biblioteca além do Compose, e cabe em 3,7 MB.

## Mesma precisão do desktop: como é garantido

O app não tem "um modelo parecido": ele lê a **mesma tabela `markets.json`** que o desktop ajusta às médias reais da
Uber e refaz as mesmas contas, na mesma ordem.

- A tabela (tarifa sem dinâmica, ritmo real sobre o OSRM, incertezas e o nível de cada estado e município) vem do
  `Oracle/markets.json` do desktop, copiada para os assets pelo `golden.py`.
- O perfil horário, o município de origem, o estado esperado, a tarifa, a calibração pelos preços informados, a
  normal acumulada e os quantis da mistura de chuva são reescritos linha a linha em Kotlin.
- `golden.py` roda o código Python do desktop e grava `app/src/test/resources/golden.json`; o `ParityTest.kt`
  confere tudo e o resultado atual é **diferença zero** nas faixas e na série do engine:

| Teste | O que compara |
|---|---|
| `holidays` | feriados ANBIMA de 2020 a 2045 |
| `clock` | dia da semana, hora e dia local em 4 fusos, inclusive o dia sem meia-noite de 2018 |
| `keys` | chave do município sem acento nem caixa, como o Nominatim e o IBGE escrevem diferente |
| `profile` | congestionamento e demanda da semana local em 500 instantes |
| `markets` | nível, ritmo, incerteza e nome do mercado em 69 origens: municípios da tabela, vizinhos, estados sem cidade perto e fora da amostra |
| `state` | dinâmica, congestionamento, minutos e tempo sem trânsito em ~4 mil instantes, 5 rotas e 2 fusos |
| `tariff` | tarifa, parte dos minutos e dinâmica dos dois aplicativos em 80 combinações |
| `quantiles` | normal acumulada e 360 quantis de misturas de chuva |
| `calibration` | nível, variância, dinâmica do preço mais recente e amostra guardada em 50 casos (nenhum, um, vários, absurdo, mais que a amostra) |
| `model` | série p10–m90 em 120 casos (5 rotas × 2 aplicativos × 6 horários, calibrada e ancorada) |
| `engine` | série completa com clima interpolado e preços informados em 40 casos |
| `durations` e `spans` | tempo de viagem em minutos ou horas, com o mesmo arredondamento e o mesmo texto |
| `starts` | ponto inicial do mapa do ◎ em 8 situações de estimativa e lugares já usados |
| `alerts` | alertas a cada 10% de afastamento, sem repetir no mesmo patamar |

## O que é diferente do desktop, e por quê

- **A tabela vem pronta do desktop.** O ajuste às médias reais e a coleta (`market.py`) rodam no computador; o celular
  lê o resultado nos assets. Os preços que você informa ficam no celular e ajustam o preço lá mesmo.
- **Worker enquanto o app está aberto ou acompanhando uma rota**, como no desktop, que só roda com a janela aberta: a
  cada slot de 10 min guarda a previsão das 5 rotas mais recentes e a compara com os preços reais informados depois.
- **Localização pelo GPS**, que é o que o desktop não tem; sem permissão ou sem sinal ele cai para a mesma
  reserva do desktop: BeaconDB e três provedores de IP, ficando com a fonte mais precisa.
- **Tempo real em serviço de primeiro plano**, para funcionar com a tela desligada.

## Estrutura

```
TarifaDinamica.apk               app pronto para instalar
golden.py                        gera o golden.json a partir do desktop e copia o markets.json para os assets
app/src/main/assets/markets.json tabela ajustada às médias reais da Uber pelo desktop
app/src/main/java/com/klauss/tarifa/
  Api/        Photon, Nominatim, OSRM (com espelho FOSSGIS), Open-Meteo e BeaconDB, com fila por servidor e retentativa
  Database/   SQLite com o mesmo esquema do desktop e a mesma migração (Fares guarda os preços reais informados)
  Engine/     rota com município de origem, clima, preço e tempo esperados agora e série calibrada
  Model/      faixas p10–p90 e m10–m90 pela mistura de chuva, com a incerteza do nível, da dinâmica e do trânsito
  Oracle/     tabela real, perfil horário, mercado esperado e calibração pelos preços informados, idênticos aos do desktop
  Worker/     previsão a cada slot e acerto medido contra os preços reais informados
  Tracker/    tempo real em primeiro plano, notificação e alertas
  Interface/  tela (Interface.kt), gráfico (Chart/), campo com autocomplete (Search/) e mapa (Locator/)
  Utils/      relógio, feriados, formatação e som dos alertas
app/src/test/java/com/klauss/tarifa/ParityTest.kt   paridade com o desktop
```

Cada componente fica em `Pasta/Pasta.kt`, e não em `index.kt` como no Python: o compilador do Compose gera uma
classe `ComposableSingletons$<Arquivo>Kt` por arquivo, e dois `index.kt` no mesmo pacote se sobrescrevem (era isso
que fechava o app ao abrir o mapa no release).

## Compilar e atualizar o modelo

```bash
export JAVA_HOME=~/Android/jdk-21 ANDROID_HOME=~/Android/Sdk
../Desktop/venv/bin/python golden.py                               # depois de mudar o desktop ou a tabela
./gradlew testDebugUnitTest                                         # paridade com o desktop
./gradlew assembleRelease && cp app/build/outputs/apk/release/app-release.apk TarifaDinamica.apk
```

- Toolchain: JDK 21, Android SDK 36, Gradle 8.14.5, AGP 8.13.2, Kotlin 2.2.21 e Compose BOM 2026.06.00 (o Compose
  1.12 exige AGP 9 e SDK 37).
- Assinatura: `release.jks` e `keystore.properties` ficam fora do git. Guarde os dois — sem a mesma chave, instalar
  uma versão nova exige desinstalar a anterior (e perder os lugares já usados). Sem esses arquivos o release sai
  assinado com a chave de debug.

## Limitações

- As mesmas do desktop: a dinâmica do instante só é conhecida quando você informa o preço, não há trânsito em tempo
  real gratuito, a tabela envelhece se não for recoletada e os servidores públicos não têm garantia de disponibilidade.
- Aparelhos com economia de bateria agressiva (Xiaomi, Samsung, Motorola) podem encerrar o acompanhamento com a tela
  desligada; se os alertas pararem, desative a otimização de bateria para o Tarifa Dinâmica.
