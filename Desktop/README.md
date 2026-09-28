# Tarifa Dinâmica

App desktop em Python (modo escuro) que mostra, para uma corrida por aplicativo, o preço e o tempo de viagem
agora e a previsão das próximas horas em três linhas no mesmo eixo de tempo: preço (p10, p50, p90), chuva
prevista (chance por hora e mm/h) e tempo de viagem previsto. Um seletor no topo escolhe entre **Uber** e **99**.
Janela ajustável de 1 a 12 h, passos de 10 min e acompanhamento em tempo real com alerta sonoro quando o preço se
afasta 10% do valor da primeira consulta. A versão Android, com a mesma tabela e os mesmos números, fica em
`../Mobile`.

> **De onde vem o preço.** Não existe API pública gratuita de preços: a página de estimativa da Uber passou a exigir
> login (manda para `m.uber.com`), o endpoint que ela usava foi desativado e a 99 nunca teve API pública. O que a
> Uber publica, sem login, são as **médias reais do último mês por trecho** (preço médio da UberX, distância e tempo
> médios das viagens). O app foi ajustado a **699 dessas médias, de 219 municípios em 19 estados, coletadas em
> 26/09/2026**: delas saem a tarifa sem dinâmica, o nível de preço de cada município de origem e quanto o tempo real
> de viagem passa do tempo sem trânsito do OSRM. A 99 sai da razão típica entre as duas (0,90). O resto é dado real e
> aberto: endereços e municípios (OpenStreetMap via Photon e Nominatim), rotas (OSRM), clima (Open-Meteo) e
> localização (Wi-Fi pelo BeaconDB e IP). **Para o valor bater com o que o app cobra agora, informe o preço real**:
> ele passa a valer exatamente naquele instante e ajusta a região.

## Como rodar

```bash
sudo apt install python3-tk python3-venv    # Tk e venv do sistema, se faltarem
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python index.py
```

- A janela abre pronta: não há treino nem histórico para gerar. Tudo o que o app escreve fica em `data/`
  (`surge.db`, `app.log` e os dois sons de alerta); a tabela ajustada fica em `Oracle/markets.json`.
- Um banco de versão anterior é migrado sozinho: as rotas que você usou (os lugares já usados) ficam, o que era
  derivado é recalculado.
- `python test.py` roda a validação completa (~20 min, com rede, abre uma janela de teste e usa banco temporário).
- `python market.py` refaz a tabela a partir dos trechos guardados em `Oracle/routes.csv` (~20 s); apague esse
  arquivo para coletar médias novas da Uber (~1 h, devagar de propósito).

## Como usar

1. Escolha o aplicativo no seletor **Uber | 99**. Trocar recalcula o preço e o gráfico na hora, sem consultar a
   rede de novo.
2. Digite origem e destino. A lista de sugestões aparece enquanto você digita, com aviso de carregando.
3. O botão ◎ de cada campo abre a janela "Onde você está?": escolha um lugar já usado, em um clique, ou marque
   o ponto exato no mapa. O mapa abre na estimativa de localização; quando ela é grosseira (por IP, quilômetros de
   erro), abre no lugar já usado mais recente dentro dessa área, e sem estimativa abre no último lugar usado ou na
   região. Arraste para mover, use a roda do mouse ou +/− para o zoom e clique no ponto exato.
4. **Calcular preço** mostra o preço estimado agora, a dinâmica estimada, o tempo de viagem, o resumo da rota e as
   três linhas do gráfico:
   - **Preço da corrida** (a maior): preço previsto com a faixa p10–p90, o preço de agora e o pico da janela;
   - **Chuva prevista**: a chance de chuva hora a hora numa faixa colorida e, logo abaixo, a intensidade em mm/h;
   - **Tempo de viagem previsto**: quanto tempo leva até o destino saindo em cada horário, com a faixa m10–m90 e
     as linhas sem trânsito, moderado e intenso.
   Passando o mouse, uma cruzeta mostra preço, tempo de viagem, hora de chegada, minutos a mais pelo trânsito e
   chuva daquele horário.
5. O controle no topo do gráfico escolhe a janela de 1 a 12 h. A série inteira já vem calculada.
6. O **tempo real** fica sempre ligado: a cada 30 s o preço da rota é recalculado. Ele fica **azul com ▼** abaixo
   do valor da primeira consulta e **vermelho com ▲** acima, e a cada 10% de afastamento toca um alerta (arpejo
   ascendente quando cai, grave descendente quando sobe), sem repetir no mesmo patamar.
7. **Informar o preço real.** Abra a Uber ou a 99 na mesma rota, veja o valor que o app cobra agora, digite-o em
   *preço real no app* e clique em **Calibrar**. Na hora o preço de agora passa a ser exatamente esse e a faixa se
   estreita; nas horas seguintes a dinâmica que ele revelou se dissipa e a parte que persiste fica no nível da
   região. A nota sob o campo diz de onde vem o preço e quantos preços seus por perto o ajustam; **0** apaga os seus
   preços daquele aplicativo.
8. A barra de status mostra a data das médias em uso e, depois que você informa preços em rotas monitoradas, quanto
   as previsões feitas antes deles erraram — o teste mais honesto da projeção, feito com os seus preços.

## Estrutura

```
index.py            ponto de entrada: log, banco, worker e janela
market.py           coleta das médias reais da Uber, ajuste da tabela e validação cruzada
test.py             validação de relógio, serviços, municípios, tabela contra os dados reais, mercado, faixas, calibração, rotas, worker e janela
Api/index.py        Photon, Nominatim (texto livre e município), OSRM (com espelho de reserva), Open-Meteo, BeaconDB e provedores de IP
Database/index.py   SQLite em WAL: Routes, Forecasts, Fares (preços reais informados) e Metrics, com migração
Engine/index.py     rota com município de origem, clima, preço e tempo esperados agora, série calibrada e registro do preço real
Market/index.py     coleta (sitemap e páginas de trecho da Uber, sedes do IBGE, OSRM) e ajuste por município com validação cruzada
Model/index.py      faixas p10–p90 e m10–m90: mistura dos cenários seco e chuvoso com a incerteza do nível, da dinâmica e do trânsito
Oracle/index.py     tabela real, perfil horário, mercado esperado e calibração bayesiana pelos preços informados
Oracle/markets.json tabela ajustada (tarifa sem dinâmica, ritmo, incertezas, nível de cada estado e município)
Oracle/routes.csv   os 699 trechos reais usados no ajuste e na validação
Worker/index.py     previsão das rotas monitoradas a cada 10 min e acerto medido contra os preços reais informados
Interface/          janela (index.py), gráfico (Chart/), campo com autocomplete (Search/) e escolha do ponto no mapa (Locator/)
Utils/              constantes, relógio com feriados e alertas sonoros
```

## Arquitetura

**Médias reais da Uber.** Cada página de trecho da Uber (`uber.com/global/pt-br/r/routes/…`, liberada pelo
`robots.txt` e listada no sitemap) traz o preço médio da UberX no último mês entre dois municípios, a distância e o
tempo médios dessas viagens e quantas foram feitas. O `market.py` sorteia trechos do sitemap (todos os estados, mais
peso no Rio de Janeiro), lê cada página a cada 2 s, localiza os municípios pelas sedes do IBGE e traça a rota de sede
a sede no OSRM. O resultado fica em `Oracle/routes.csv`, com a data da coleta.

**Tarifa e nível por município.** O preço médio de cada trecho é o nível do município de origem vezes a tarifa sem
dinâmica — bandeirada, km, minuto e um valor por km acima de 40 km, porque a viagem intermunicipal volta vazia —
aplicada ao tempo de cada hora da semana, vezes a dinâmica daquela hora, ponderado pelo volume de viagens. O nível
de cada município é encolhido para o do estado e o do estado para o do país (crista), então um município com poucos
trechos fica perto do estado. A tarifa nacional sem dinâmica saiu em **R$ 3,00 + R$ 1,21/km + R$ 0,31/min
(+ R$ 0,22/km acima de 40 km)**, mínima de R$ 10, no nível nacional; o nível dos municípios vai de 0,84 (São José/SC) a
1,40 (Barueri/SP); o Rio de Janeiro fica em 1,06, São Paulo em 1,35 e Macaé em 0,89. A origem
de cada rota é localizada pelo Nominatim no nível de município (a fronteira do OSM), uma vez por rota: Ilha do
Governador conta como Rio de Janeiro, Xerém como Duque de Caxias. Município sem trecho publicado usa a média dos
vizinhos do mesmo estado, puxada para o estado.

**Tempo de viagem.** O OSRM dá o tempo sem trânsito, otimista: nas médias reais, a viagem urbana curta leva até o
dobro e a rodovia rápida uns 15% a mais. O ritmo real sobre o OSRM foi ajustado pelo tamanho do trecho, pela
velocidade que o OSRM supõe e pelo município e estado de origem (erro mediano de 7,8% fora da amostra). Ao longo da
semana ele segue o **congestionamento real hora a hora do TomTom Traffic Index 2025**, média das 9 metrópoles
brasileiras do índice (Rio, São Paulo, Belo Horizonte, Recife, Porto Alegre, Fortaleza, Curitiba, Salvador e
Brasília, com correlação de 0,97 entre elas): 60–65% de tempo a mais às 7–8 h, 40–47% no meio do dia, 73–77% às
17–18 h nos dias úteis, 40% no sábado ao meio-dia e 1–5% de madrugada; de madrugada fica um quarto do atraso médio
(semáforos, conversões, vias lentas). Chuva forte alonga a viagem até 25%. O tempo não muda com o aplicativo.

**Dinâmica.** A demanda da semana local (picos de manhã e de tarde, sexta à noite, madrugadas de fim de semana,
domingo à tarde, feriados valendo como domingo) e a chuva formam o excesso de demanda; a dinâmica é 1 + esse excesso
(até 1,35× no pico mais forte, até 0,40 a mais com chuva forte), limitada a 2,5×. A média do mês ponderada pelo
volume de viagens é a que o ajuste casa com a Uber, então a tarifa sem dinâmica e o perfil somam o preço real médio.

**Faixas.** A faixa p10–p90 junta tudo o que não se sabe, cada coisa com o seu tamanho: o nível da região (o erro da
validação cruzada: 11% num município da tabela, 13% num município novo), a dinâmica do instante (10% fora do pico,
mais no pico e na chuva), o tempo de viagem no preço por minuto, e a chuva pela **chance real do Open-Meteo**: cada
instante é uma mistura do cenário seco e do chuvoso, com o peso da chance, e os quantis saem da mistura por bisseção.
Mais chuva ou mais chance nunca reduzem nenhum quantil, e p10 < p50 < p90 sempre.

**Preço real informado.** Cada preço guarda o preço central que o app mostrava sem calibração naquele instante e o
nível da região usado. O desvio entre os dois se divide em duas partes, pela estatística bayesiana: o **nível** da
região (persistente; prioriza a incerteza da tabela e pesa cada preço pela distância da origem, meia-vida de ~35 km)
e a **dinâmica** do instante, que vale inteira por 2 min e se dissipa com constante de 45 min. Por isso o preço
informado aparece exato na hora, a faixa se estreita e, três horas depois, sobra só a parte que persiste (com um
preço 25% acima, ~13%). Contam os 20 preços mais recentes de cada aplicativo; um preço fora de metade a dobro do
esperado é tratado como erro de digitação; o preço de um aplicativo não mexe no outro.

**Worker.** Uma thread alinhada aos slots de 10 min guarda a previsão das próximas 12 h das 5 rotas usadas mais
recentemente, nos dois aplicativos, e compara as previsões feitas antes de cada preço informado com ele: erro médio
e cobertura da faixa vão para a tabela `Metrics` e para a barra de status. É a medida real, com os seus preços, de
quanto a projeção acerta.

**Interface.** CustomTkinter 6 com matplotlib embutido. O painel lateral rola quando a janela é baixa (cabe em
notebook de 768 px) e o status e os títulos quebram linha em janela estreita. A chuva tem linha própria com o mesmo
eixo de tempo: a chance (%) numa faixa colorida hora a hora e a intensidade (mm/h) logo abaixo. Não há caixas de
legenda: o título de cada linha diz o que ela mostra. As cores das séries foram validadas contra o fundo escuro.

**Nada de rede na thread do Tk.** Toda consulta roda em um `ThreadPoolExecutor`; o resultado volta por uma fila
lida a cada 50 ms com `after`, e só esse callback mexe nos widgets.

**Endereços.** O autocomplete usa o Photon, que é construído sobre o OpenStreetMap e casa prefixos:
"Rua Professor Antônio Ava" já encontra a rua de origem, que no OSM está grafada "Avarez Parada". O Nominatim
fica para validar texto livre, em uma consulta por clique, e para achar o município da origem, uma vez por rota: a
política de uso dele proíbe autocomplete. A busca espera 350 ms de pausa na digitação e descarta respostas antigas.

**Localização atual.** Sem GPS, nenhuma fonte gratuita acerta a rua. O app dispara em paralelo o BeaconDB (redes
Wi-Fi visíveis, lidas pelo `nmcli`) e três provedores de IP sem cadastro (geojs, ipwho.is e ipinfo), tira a mediana
das coordenadas deles e usa a dispersão como erro declarado; vale a fonte mais precisa. Por isso o ◎ abre a janela
"Onde você está?", com os lugares já usados (exatos, um clique) e um mapa escuro que se arrasta. Com estimativa
precisa o mapa abre nela; com estimativa grosseira, no lugar já usado mais recente que cabe no erro dela; sem
estimativa, no último lugar usado ou na região. O clique marca o ponto na hora e o ponto vira endereço pelo Photon
reverso. Os tiles vêm do OpenTopoMap (CC-BY-SA), com a OSM France de reserva, invertidos em tons de cinza.

**Rota e clima.** O OSRM entrega distância e duração sem trânsito; quando o servidor de demonstração falha, a mesma
consulta vai ao espelho da FOSSGIS. Pontos a mais de 2 km de qualquer via são recusados (ilhas, mar aberto). O
Open-Meteo dá a previsão horária de chuva e da chance de chuva e o fuso de cada coordenada; a chuva de cada hora é a
soma da hora anterior, então a taxa é centrada 30 min antes. Se o Open-Meteo cair, a última previsão em cache segue
valendo enquanto cobrir as 12 h da série; depois disso a consulta avisa em vez de desenhar chuva inventada.

**Fuso por rota.** Demanda e trânsito seguem o relógio do lugar da corrida, não o do computador. Cada rota guarda o
fuso da origem, e o gráfico avisa quando ele difere do relógio local.

## O que foi medido

Tudo abaixo sai do `test.py` (120 verificações, todas passando), contra as médias reais da Uber de 26/09/2026, com
validação cruzada: o trecho (ou o município inteiro) que é previsto fica fora do ajuste.

| Preço médio da UberX por trecho | erro mediano | viés mediano | 90% dos trechos com erro até |
|---|---|---|---|
| tabela antiga do app (calibrada em Macaé) | 21,2% | **−21,2%** | 39% |
| novo, município da tabela | **7,0%** | 0,0% | 19,4% |
| novo, município sem trecho publicado (só o estado) | 8,5% | −0,1% | 22% |

- **Tempo médio real de viagem** previsto a partir do OSRM: erro mediano de 7,8% em 594 trechos.
- **Faixa do nível regional**: cobre 80% das médias reais fora do ajuste, o nominal.
- **Cadeia inteira do app** (município, ritmo, perfil horário e tarifa, como na tela) contra a média real do mês:
  erro mediano de 5,9% no preço (viés −0,1%) e de 6,9% no tempo.
- Por estado, o erro do app fica entre 3% e 7% onde há dezenas de trechos (RJ 5,9%, SP 7,1%, RS 5,9%, MG 5,9%, PE
  4,7%, SC 4,1%, PR 4,1%); a tabela antiga errava 18% no RJ e 34% em SP.

**Rotas de verificação** (preço de agora num sábado às 19 h, nos dois aplicativos, com o município identificado):

| Rota | km | sem trânsito | agora | município | Uber | 99 |
|---|---|---|---|---|---|---|
| Copacabana → Ipanema (RJ) | 5,1 | 7 min | 12 min | Rio de Janeiro/RJ | R$ 14,70 | R$ 13,23 |
| Paulista → Ibirapuera (SP) | 5,3 | 13 min | 16 min | São Paulo/SP | R$ 21,24 | R$ 19,11 |
| Ilha do Governador → Xerém | 34,3 | 39 min | 53 min | Rio de Janeiro/RJ | R$ 69,86 | R$ 62,88 |
| Rio → Niterói (ponte) | 16,4 | 20 min | 28 min | Rio de Janeiro/RJ | R$ 36,31 | R$ 32,68 |
| Rio → São Paulo | 431,7 | 5h39 | 6h18 | Rio de Janeiro/RJ | R$ 835,20 | R$ 751,68 |
| Manaus centro → aeroporto | 15,2 | 20 min | 25 min | média nacional | R$ 30,97 | R$ 27,87 |

Da Ilha do Governador a Xerém a tabela antiga dava cerca de R$ 55. Como referência real, a média da Uber do Rio para
Duque de Caxias é R$ 47 em 22 km e 34 min, e a do Rio para Guarulhos é R$ 865 em 419 km, 3,5% acima dos R$ 835 do
app para São Paulo (os pedágios, que o OSRM público não identifica, ficam só na média).

**Estrutura:** sexta 18 h custa 40% mais e leva 31% mais que a madrugada; feriado de manhã custa 20% menos que uma
segunda comum; chuva forte no pico encarece 32%; mais chuva nunca barateia nem acelera; a 99 fica a 0,90 da Uber
em todo instante, com o mesmo tempo de viagem.

**Preço informado:** aparece exato na hora (pedido R$ 85,16, mostrado R$ 85,16) com a faixa estreita; com um preço
25% acima do esperado, três horas depois sobra 13% (o nível aprendido); um preço a 300 km quase não mexe em outra
origem; um preço absurdo fica preso no dobro do esperado; 25 preços 10% acima convergem o nível para 1,09.

## Atualizar a tabela

As médias da Uber mudam: pelo IPCA do IBGE, o transporte por aplicativo subiu 57% no Brasil em 24 meses (67% na
região metropolitana do Rio), com alta forte em dezembro e queda em janeiro. Para renovar:

```bash
rm Oracle/routes.csv
python market.py                                  # ~1 h de coleta devagar e o ajuste; imprime a validação
python test.py                                    # confere a tabela nova contra os dados reais
cd ../Mobile && ../Desktop/venv/bin/python golden.py && ./gradlew testDebugUnitTest
```

Entre uma coleta e outra, os preços que você informa ajustam o nível da sua região.

## Limitações

- **A dinâmica do instante não é pública.** A tabela acerta o preço médio de cada região; num instante de dinâmica
  alta que o perfil horário não previu (show, pane, promoção), o app só fica exato depois que você informa o preço.
- O trânsito hora a hora é o típico real das metrópoles (TomTom), não o de cada rota; a dinâmica hora a hora é o
  padrão de demanda da semana, sem fonte pública medida: as médias da Uber são mensais. A barra de status mede o
  acerto hora a hora com os seus preços.
- A 99 não publica preços: ela sai da razão típica de 0,90 sobre a UberX até você informar preços da 99.
- Pedágios não são identificados pelo OSRM público; eles entram só na média dos trechos que os têm.
- Não há trânsito em tempo real gratuito; a faixa inclui o erro típico do tempo, não um acidente de hoje.
- A localização automática erra quilômetros sem GPS; use o mapa ou os lugares já usados.
- O OSRM público, o Nominatim e o Open-Meteo não têm garantia de disponibilidade. Rotas já consultadas ficam em
  cache no banco, com o município.
