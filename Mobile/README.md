# Tarifa Dinâmica — Android

App Android que faz o mesmo que o app de desktop (`../Desktop`): preço e tempo de viagem agora e a previsão das
próximas 12 h em passos de 10 min (p10, p50, p90), com chuva prevista e tempo de viagem previsto no mesmo eixo de tempo,
acompanhamento em tempo real a cada 30 s e alerta sonoro a cada 10% de afastamento do preço da primeira consulta.

> **Os preços são sintéticos**, como no desktop: não existe API pública gratuita de preços da Uber ou da 99, então o
> "preço real" vem do oráculo que simula o mercado. Endereços (OpenStreetMap via Photon e Nominatim), rotas (OSRM),
> clima (Open-Meteo) e localização (GPS do celular, ou BeaconDB) são dados reais.

## Instalar

1. Copie `TarifaDinamica.apk` (3,7 MB) para o celular.
2. Abra o arquivo e permita "instalar apps desconhecidos" para o gerenciador de arquivos quando o Android pedir.
3. Android 8.0 ou mais novo. O app pede localização (só ao tocar em ◎) e notificações (para o tempo real e os alertas).

## Como usar

1. Digite origem e destino e escolha uma sugestão (a borda fica verde quando o endereço está validado).
2. O ◎ lê o GPS e abre o mapa "Onde você está?" na posição: toque no ponto exato ou escolha um lugar já usado.
   Sem permissão ou sem sinal de GPS, usa a estimativa por IP e, como ela erra quilômetros, abre no lugar já usado
   mais recente dentro do erro; sem nenhuma estimativa, no último lugar usado ou na região. Arraste para mover o
   mapa e use a pinça (ou +/−) para o zoom — o mesmo critério do desktop.
3. **Calcular preço** mostra o preço agora, a variação desde a primeira consulta, a faixa dos próximos 10 min, o
   resumo da rota e o gráfico: preço com a faixa p10–p90, chuva prevista e **tempo de viagem previsto** em minutos
   (ou horas), com as linhas sem trânsito, moderado e intenso. Arraste o dedo no gráfico para a cruzeta, que mostra
   também a hora de chegada; a barra escolhe a janela de 1 a 12 h.
4. O **tempo real** continua com o app fechado: uma notificação fixa mostra o preço e a variação e tem o botão
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

O app não tem "um modelo parecido": ele lê o **mesmo `model.json`** que o desktop treina e refaz as mesmas contas.

- As árvores do LightGBM são lidas do texto do próprio `model_to_string` e somadas na ordem do LightGBM.
- O oráculo reproduz o `numpy.random.default_rng` bit a bit (SeedSequence → PCG64 → ziggurat, com as tabelas do
  numpy), então o mesmo instante dá o mesmo preço e o mesmo tempo no celular e no computador.
- `golden.py` roda o código Python do desktop e grava `app/src/test/resources/golden.json`; o `ParityTest.kt`
  confere tudo e o resultado atual é **diferença zero** em:

| Teste | O que compara |
|---|---|
| `rng` | bits crus, 2 000 normais, Poisson e uniformes em 5 sementes |
| `holidays` | feriados ANBIMA de 2020 a 2045 |
| `clock` | dia da semana, hora e dia local em 4 fusos, inclusive o dia sem meia-noite de 2018 |
| `random` | acidentes, ruído de preço e de tempo do oráculo |
| `market` | preço e tempo do oráculo em ~7 mil instantes, 4 rotas e 4 fusos |
| `quantiles` | soma das árvores e quantis rearranjados na chuva em 60 linhas aleatórias |
| `model` | série p10–m90 em 96 casos (4 rotas × 6 horários × 4 regimes, inclusive acidente) |
| `engine` | série completa com clima interpolado e observações do dia em 16 casos |
| `durations` e `spans` | tempo de viagem em minutos ou horas, com o mesmo arredondamento e o mesmo texto |
| `starts` | ponto inicial do mapa do ◎ em 8 situações de estimativa e lugares já usados |
| `alerts` | alertas a cada 10% de afastamento, sem repetir no mesmo patamar |

## O que é diferente do desktop, e por quê

- **Não treina no celular.** O LightGBM não roda no Android, então o modelo vem treinado do desktop. O oráculo é
  estacionário: retreinar com mais algumas horas de observação não muda a previsão (no desktop, as versões 1 e 2 do
  modelo tiveram o mesmo erro de calibração, 2,15% e 3,50%).
- **Worker enquanto o app está aberto ou acompanhando uma rota**, como no desktop, que só observa com a janela
  aberta: a cada slot de 10 min observa as 5 rotas mais recentes, guarda as previsões e as consolida.
- **Localização pelo GPS**, com a estimativa por IP como reserva.
- **Tempo real em serviço de primeiro plano**, para funcionar com a tela desligada.

## Estrutura

```
TarifaDinamica.apk               app pronto para instalar
golden.py                        gera o golden.json a partir do desktop e copia o model.json para os assets
app/src/main/assets/model.json   modelo treinado pelo desktop
app/src/main/java/com/klauss/tarifa/
  Api/        Photon, Nominatim, OSRM (com espelho FOSSGIS), Open-Meteo e BeaconDB, com fila por servidor e retentativa
  Database/   SQLite com o mesmo esquema do desktop
  Engine/     rota, clima, preço e tempo observados agora e série ancorada
  Model/      árvores do LightGBM, rearranjo na chuva, âncora com corte de choque e faixa conformal
  Oracle/     mercado simulado, idêntico ao do desktop
  Worker/     observação a cada slot e consolidação das previsões
  Tracker/    tempo real em primeiro plano, notificação e alertas
  Interface/  tela (Interface.kt), gráfico (Chart/), campo com autocomplete (Search/) e mapa (Locator/)
  Utils/      relógio e feriados, gerador do numpy, tabelas do ziggurat e som dos alertas
app/src/test/java/com/klauss/tarifa/ParityTest.kt   paridade com o desktop
```

Cada componente fica em `Pasta/Pasta.kt`, e não em `index.kt` como no Python: o compilador do Compose gera uma
classe `ComposableSingletons$<Arquivo>Kt` por arquivo, e dois `index.kt` no mesmo pacote se sobrescrevem (era isso
que fechava o app ao abrir o mapa no release).

## Compilar e atualizar o modelo

```bash
export JAVA_HOME=~/Android/jdk-21 ANDROID_HOME=~/Android/Sdk
../Desktop/venv/bin/python golden.py ../Desktop/data/model.json    # depois de retreinar ou mudar o desktop
./gradlew testDebugUnitTest                                         # paridade com o desktop
./gradlew assembleRelease && cp app/build/outputs/apk/release/app-release.apk TarifaDinamica.apk
```

- Toolchain: JDK 21, Android SDK 36, Gradle 8.14.5, AGP 8.13.2, Kotlin 2.2.21 e Compose BOM 2026.06.00 (o Compose
  1.12 exige AGP 9 e SDK 37).
- Assinatura: `release.jks` e `keystore.properties` ficam fora do git. Guarde os dois — sem a mesma chave, instalar
  uma versão nova exige desinstalar a anterior (e perder os lugares já usados). Sem esses arquivos o release sai
  assinado com a chave de debug.

## Limitações

- As mesmas do desktop: preços sintéticos, sem trânsito em tempo real gratuito, faixa que assume a previsão de chuva
  correta e servidores públicos sem garantia de disponibilidade.
- Aparelhos com economia de bateria agressiva (Xiaomi, Samsung, Motorola) podem encerrar o acompanhamento com a tela
  desligada; se os alertas pararem, desative a otimização de bateria para o Tarifa Dinâmica.
