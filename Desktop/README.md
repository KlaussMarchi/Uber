# Tarifa Dinâmica

App desktop em Python (modo escuro) que mostra, para uma corrida por aplicativo, o preço e o tempo de viagem
agora e a previsão das próximas horas em três linhas no mesmo eixo de tempo: preço (p10, p50, p90), chuva
prevista (chance por hora e mm/h) e tempo de viagem previsto. Janela ajustável de 1 a 12 h, passos de 10 min e
acompanhamento em tempo real com alerta sonoro quando o preço se afasta 10% do valor da primeira consulta.
A versão Android, com o mesmo modelo e os mesmos números, fica em `../Mobile`.

> **Os preços são sintéticos.** Não existe API pública gratuita de preços da Uber ou da 99, então o "preço real"
> vem de um oráculo que simula o mercado (`Oracle/`). O resto é dado real e aberto: endereços (OpenStreetMap via
> Photon e Nominatim), rotas (OSRM), clima (Open-Meteo) e localização (BeaconDB). O modelo aprende o oráculo, não
> a tarifa verdadeira da Uber.

## Como rodar

```bash
sudo apt install python3-tk python3-venv    # Tk e venv do sistema, se faltarem
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python index.py
```

- No primeiro boot a janela abre na hora e o worker leva ~2 min para baixar rotas e um ano de chuva, gerar o
  histórico e treinar.
- Tudo fica em `data/`: `surge.db` (SQLite), `model.json`, `app.log` e os dois sons de alerta.
- Para recomeçar do zero, apague `data/`. Se o esquema, o oráculo ou as rotas do bootstrap mudarem de versão, o
  banco é recriado sozinho.
- `python test.py` roda a validação completa (~12 min, com rede, abre uma janela de teste e usa banco temporário).
- `python bootstrap.py` gera só o histórico sintético, sem abrir a janela.

## Como usar

1. Digite origem e destino. A lista de sugestões aparece enquanto você digita, com aviso de carregando.
2. O botão ◎ de cada campo abre a janela "Onde você está?": escolha um lugar já usado, em um clique, ou marque
   o ponto exato no mapa. O mapa abre na estimativa de localização; quando ela é grosseira (por IP, quilômetros de
   erro), abre no lugar já usado mais recente dentro dessa área, e sem estimativa abre no último lugar usado ou na
   região. Arraste para mover, use a roda do mouse ou +/− para o zoom e clique no ponto exato.
3. **Calcular preço** mostra o preço e o tempo de viagem de agora, o resumo da rota e as três linhas do gráfico:
   - **Preço da corrida** (a maior): preço previsto com a faixa p10–p90, o preço observado agora e o pico da janela;
   - **Chuva prevista**: a chance de chuva hora a hora numa faixa colorida e, logo abaixo, a intensidade em mm/h;
   - **Tempo de viagem previsto**: quanto tempo leva até o destino saindo em cada horário, em minutos (ou horas nas
     rotas longas), com a faixa m10–m90 e as linhas sem trânsito, moderado e intenso.
   Passando o mouse, uma cruzeta mostra preço, tempo de viagem, hora de chegada, minutos a mais pelo trânsito e
   chuva daquele horário.
4. O controle no topo do gráfico escolhe a janela de 1 a 12 h. A série inteira já vem calculada, então trocar a
   janela não recalcula nada nem muda a precisão.
5. O **tempo real** fica sempre ligado, sem botão: a cada 30 s o preço da rota na tela é observado de novo:
   - o preço fica **azul com ▼** quando está abaixo do valor da primeira consulta da rota e **vermelho com ▲**
     quando está acima, sempre com a porcentagem acumulada;
   - a cada 10% de afastamento toca um alerta: arpejo ascendente quando cai, grave descendente quando sobe;
   - o alerta não se repete no mesmo patamar; toca de novo quando passa o próximo múltiplo de 10%.

## Estrutura

```
index.py            ponto de entrada: log, worker e janela
bootstrap.py        gerador do histórico sintético do primeiro boot (14 rotas reais)
test.py             validação de relógio, serviços, distâncias, oráculo, cenários, precisão, rotas fora do treino, acidentes, worker e janela
Api/index.py        Photon, Nominatim, OSRM (com espelho de reserva), Open-Meteo e BeaconDB, com fila por servidor e retentativa
Database/index.py   SQLite em WAL: Routes, Prices, Forecasts, Metrics
Engine/index.py     rota, clima, preço e tempo observados agora e série ancorada
Model/index.py      LightGBM quantílico, âncora por horizonte com corte de choque e calibração conformal
Oracle/index.py     mercado simulado que define o preço "real"
Worker/index.py     oráculo a cada 10 min, consolidação das previsões e retreino
Interface/          janela (index.py), gráfico (Chart/), campo com autocomplete (Search/) e escolha do ponto no mapa (Locator/)
Utils/              constantes, relógio com feriados e alertas sonoros
```

## Arquitetura

**Interface.** CustomTkinter 6 com matplotlib embutido. O Tk já vem no Linux e não depende de plugin de
plataforma como o Qt (xcb). A chuva tem linha própria com o mesmo eixo de tempo: a chance (%) numa faixa
colorida hora a hora e a intensidade (mm/h) logo abaixo, cada uma com a sua escala, porque dois eixos y no mesmo
gráfico confundem as escalas. Não há caixas de legenda: o título de cada linha diz o que ela mostra. As cores das
séries foram validadas contra o fundo escuro (luminância, daltonismo e contraste).

**Nada de rede na thread do Tk.** Toda consulta roda em um `ThreadPoolExecutor`; o resultado volta por uma fila
lida a cada 50 ms com `after`, e só esse callback mexe nos widgets.

**Endereços.** O autocomplete usa o Photon, que é construído sobre o OpenStreetMap e casa prefixos:
"Rua Professor Antônio Ava" já encontra a rua de origem, que no OSM está grafada "Avarez Parada". O Nominatim
fica para validar texto livre, em uma consulta por clique: a política de uso dele proíbe autocomplete e, nos
testes, ele não encontra palavras incompletas. A busca espera 350 ms de pausa na digitação e descarta respostas
de buscas antigas.

**Localização atual.** Sem GPS, nenhuma fonte gratuita acerta a rua. Medi todas nesta conexão: o erro vai de
5 km (ip-api, geojs, DB-IP) a 39 km (ipapi.co), e o BeaconDB por Wi-Fi não tem cobertura em Macaé, caindo para
IP com ~25 km declarados. Por isso o ◎ não preenche sozinho: ele abre a janela "Onde você está?", com os lugares
que você já confirmou (exatos, um clique) e um mapa escuro que se arrasta. Com estimativa precisa o mapa abre nela;
com estimativa grosseira, abre no nível da rua no lugar já usado mais recente que cabe no erro dela, porque quem
já usou o app em casa quase sempre está perto de onde já esteve; sem estimativa, abre no último lugar usado ou na
região. O clique marca o ponto na hora — "Usar este ponto" nunca pega o ponto anterior, e um endereço atrasado de
outro clique é descartado — e o ponto vira endereço pelo Photon reverso. Os lugares já usados são só os que você
escolheu: as rotas sintéticas do bootstrap ficam de fora. Os tiles vêm do OpenTopoMap (CC-BY-SA), com a OSM France
de reserva, porque os servidores principais do OSM recusam quem não é navegador e o CARTO passou a exigir chave; o
app inverte as imagens em tons de cinza para combinar com o tema. O Android usa exatamente o mesmo critério, com o
GPS do celular no lugar da estimativa por IP.

**Rota e clima.** O OSRM entrega distância, duração sem trânsito e os trechos, de onde sai a fração do percurso
na Rodovia Amaral Peixoto (RJ-106) — 62% na rota de teste. O servidor público do OSRM é de demonstração; quando
ele falha, a mesma consulta vai ao espelho da FOSSGIS, que usa o mesmo mapa e devolve a mesma rota (34,08 km e
42,7 min). Pontos a mais de 2 km de qualquer via são recusados, o que evita "rotas" absurdas para ilhas e mar
aberto. O Open-Meteo dá a previsão horária de chuva e da chance de chuva, o arquivo ERA5 do último ano (que não
tem chance) e o fuso de cada coordenada; a chuva de cada hora é a soma da hora anterior, então a taxa é centrada
30 min antes. Se o Open-Meteo cair, a última previsão em cache segue valendo enquanto cobrir as 12 h da série;
depois disso a consulta avisa em vez de desenhar chuva inventada.

**Fuso por rota.** Demanda e trânsito seguem o relógio do lugar da corrida, não o do computador. Cada rota
guarda o fuso da origem, e o gráfico avisa quando ele difere do relógio local. Num dia de horário de verão em que
a meia-noite não existe, o dia começa no primeiro instante que existe (o Brasil já teve isso e pode voltar a ter).

**Worker.** Uma thread alinhada aos slots de 10 min faz, a cada ciclo (e na hora, quando a consulta traz uma rota
nova, para ela já entrar nas rotas monitoradas):

1. Observa o preço e o tempo de viagem do oráculo nas 5 rotas usadas mais recentemente; as sintéticas do bootstrap
   já têm um ano de histórico e ficam de fora.
2. Guarda a previsão das próximas 12 h de cada uma, de preço e de tempo.
3. Consolida as previsões passadas contra o que foi observado: erro médio, cobertura das faixas e perda pinball
   das últimas 24 h, para preço e tempo, na tabela `Metrics` e na barra de status. A barra só mostra a taxa de
   acerto a partir de 50 previsões consolidadas e, antes disso, mostra a contagem (com 3 previsões a faixa chegou a
   marcar 33%).
4. Retreina a cada 1 h com todo o histórico acumulado (~1 min com as 736 mil linhas).

O LightGBM libera o GIL durante o treino, então a janela continua responsiva. Um erro inesperado vai para o log
e o ciclo seguinte tenta de novo. A cobertura da barra de status mede poucas dezenas de previsões correlacionadas:
durante um acidente simulado ela cai por algumas dezenas de minutos (hoje, às 11:50, marcou 46% no preço) e volta.

**Modelo.** Três camadas, cada uma resolvendo um problema diferente:

- **Dois alvos, mesma máquina.** O modelo prevê o preço e o tempo de viagem com a mesma arquitetura: cada um
  aprende o multiplicador sobre a sua base (tarifa sem dinâmica para o preço, tempo sem trânsito para a viagem),
  porque árvores não extrapolam distância mas o multiplicador vale para rotas de qualquer tamanho.
- **Estrutura (LightGBM quantílico).** Atributos: hora local contínua, dia da semana **com feriado valendo como
  domingo**, distância, duração livre, fração no corredor e chuva.
- **Âncora (preço observado agora + nível do dia).** Numa regressão por horizonte entram o resíduo de agora,
  cortado em 2,5 desvios; o excesso acima do corte, com coeficiente próprio; e a média encolhida dos resíduos
  cortados de hoje, que vale só até a meia-noite local. O corte separa o choque (um acidente no corredor, que
  persiste e se dissipa em ~45 min) do ruído, e impede que o acidente da manhã vire "nível do dia" e encareça a
  previsão da tarde. A média crua do dia confunde nível com hora do dia; a encolhida não.
- **Faixa (conformal por horizonte e por regime).** Os offsets de p10 e p90 são calibrados em 20% dos dados,
  reservados em blocos de 7 dias para que os horizontes que atravessam a meia-noite fiquem dentro da calibração,
  separados por horizonte e por quantas observações do dia existem (rota nova, rastreada há pouco, rastreada),
  de modo que a cobertura fique nos 80% nominais.

Ainda no modelo: mais chuva nunca barateia, porque cada quantil é rearranjado sobre uma grade de chuva
(Chernozhukov et al.) — o LightGBM recusa restrição monotônica com objetivo quantílico. Os três quantis são
ordenados, e a largura mínima de 1% garante p10 < p50 < p90 em qualquer ponto.

**Oráculo.** Tarifa calibrada para a mediana da rota de teste em dia útil, das 9 às 16 h e sem chuva, dar
R$ 54,85. Demanda por picos gaussianos na semana local, com feriado seguindo o perfil de domingo; atraso de
trânsito proporcional à fração na Amaral Peixoto; acidentes em processo de Poisson com dissipação exponencial;
efeito da chuva saturando por volta de 4 mm/h. O mesmo atraso alonga a viagem e entra na tarifa, então preço e
tempo são coerentes entre si. Cada um tem a sua variação própria: oferta de motoristas por slot e nível por dia
no preço, semáforos e fila por slot mais condição do dia (obra, férias, evento) no tempo. Semente fixa: o mesmo
instante sempre dá o mesmo preço e o mesmo tempo, no desktop e no Android.

**Bootstrap.** Catorze rotas reais da região, de 4 a 186 km e de 0 a 97% no corredor, incluindo rotas longas fora
dele (Glicério → Macaé, 49 km e 0%; Macaé → Rio, 186 km e 0%) e curtas dentro dele (Unamar → Rio das Ostras, 13 km e
97%). Com as 5 rotas originais toda rota longa tinha ~62% no corredor, e o modelo aprendeu "longa = congestionada":
numa rota de 30 km fora do corredor o tempo previsto saía 2,4% alto e a faixa cobria só 60%. Sobre um ano de chuva
real somam-se 6 tempestades sintéticas por semana: sem elas a chuva real quase nunca traz temporal no horário de
pico e o modelo errava −11% nesse regime; com elas, erra cerca de 1%. São 736 mil preços, um a cada 10 min.

## O que foi medido

Tudo abaixo sai do `test.py`, contra o oráculo, em dias e rotas fora do treino.

**Precisão por horizonte** (8 dias futuros, as 14 rotas do bootstrap, âncoras a cada 3 h):

| Preço | agora | 10–30 min | 40–60 min | 1–3 h | 3–6 h | 6–12 h |
|---|---|---|---|---|---|---|
| Sem âncora | 2,5% | 2,3% | 2,3% | 2,4% | 2,4% | 2,4% |
| Rota rastreada | **0%** | 2,1% | 2,1% | 2,2% | 2,2% | 2,3% |
| Rota nova (1 observação) | **0%** | 2,2% | 2,3% | 2,3% | 2,3% | 2,3% |

| Tempo de viagem | agora | 10–30 min | 40–60 min | 1–3 h | 3–6 h | 6–12 h |
|---|---|---|---|---|---|---|
| Sem âncora | 3,7% | 3,5% | 3,5% | 3,6% | 3,6% | 3,6% |
| Rota rastreada | **0%** | 3,3% | 3,2% | 3,4% | 3,4% | 3,5% |
| Rota nova (1 observação) | **0%** | 3,5% | 3,4% | 3,5% | 3,6% | 3,6% |

A cobertura das faixas fica entre 80% e 82% em todos os horizontes e regimes, nos dois alvos. O piso de erro é a
variação do próprio mercado simulado — cerca de 2,0% no preço e 3,2% no tempo — então as duas previsões estão
perto do limite do que é previsível. Por hora do dia e por dia da semana (21 dias, âncora de hora em hora) o erro
não tem pontos cegos: fica entre 1,9% e 2,4% no preço em qualquer hora, e feriados, que são raros no treino,
erram 2,4% contra 2,1–2,3% dos dias comuns.

**Rotas fora do treino** (6 dias, âncoras a cada 3 h, rota rastreada):

| Rota | Erro preço | Erro tempo | Viés tempo | Faixa preço | Faixa tempo |
|---|---|---|---|---|---|
| 2 km, sem corredor | 2,0% | 3,3% | +0,2% | 79% | 81% |
| 9 km, 30% no corredor | 2,1% | 3,6% | +0,1% | 83% | 78% |
| 30 km, sem corredor | 2,1% | 3,5% | +0,5% | 79% | 78% |
| 30 km, 90% no corredor | 2,6% | 4,0% | −0,2% | 79% | 80% |
| 100 km, sem corredor | 2,1% | 3,1% | −0,1% | 82% | 83% |
| 300 km, 30% no corredor | 2,3% | 3,3% | −0,3% | 77% | 79% |

**Acidentes no corredor** (60 dias, 3 rotas com mais de 55% na RJ-106, consultas feitas com o acidente em curso):

| Horizonte | Erro tempo | Viés tempo | Faixa tempo | Erro preço | Viés preço | Faixa preço |
|---|---|---|---|---|---|---|
| primeira hora | 5,1% | −3,2% | 63% | 3,2% | −2,1% | 64% |
| 1–3 h | 3,8% | −0,4% | 78% | 2,5% | −0,1% | 76% |
| 3–12 h | 3,6% | +0,4% | 79% | 2,4% | +0,4% | 78% |

O começo de um acidente é imprevisível; a âncora com corte segue o choque e o dissipa. Na comparação nos mesmos
dados com a âncora anterior, a primeira hora tinha viés de −6,4% no tempo e faixa cobrindo 45%, e o resto do dia
ficava 1,7% caro porque o acidente contaminava o nível do dia.

**Chuva prevista.** A faixa assume que a previsão de chuva acerta. Em 90 dias de previsões reais do Open-Meteo
(previsão da véspera contra a mais recente), nos slots com chuva a faixa de preço cobre 50% e o erro do preço
dobra (2,2% → 4,2%); nos slots secos nada muda. Quando a chance de chuva está alta, trate o p90 como otimista.

**Cenários de horário e chuva** (previsão de 1 h à frente contra o que o oráculo cobra): erro médio de 2,28% no
preço e 2,31% no tempo, com o valor real dentro da faixa em 7 dos 8 cenários em ambos — madrugada, meio da manhã,
rush de quarta, rush de sexta seco e com chuva forte, domingo à tarde, feriado de 7 de setembro e segunda comum.

**Distância e tempo:** a rota de teste dá 34,1 km e 42,7 min, confirmados pelo espelho da FOSSGIS, que também
assume a consulta quando o servidor principal está fora do ar; a distância rodoviária fica entre 1 e 3 vezes a
linha reta; as velocidades médias das rotas testadas vão de 28 a 80 km/h; ponto no mar e ilha sem ligação
rodoviária são recusados.

**Qualquer rota:** Copacabana → Ipanema, Paulista → Ibirapuera, Rio → Niterói, Rio → São Paulo (431 km) e
Manaus centro → aeroporto (fuso `America/Manaus`) passam com fuso certo, série de 73 pontos e preço coerente com
a tarifa.

## Limitações

- Os preços vêm do oráculo sintético. Trocar `Oracle.getMarket` por uma fonte real de preços é o que falta para
  prever tarifas de verdade; a Uber restringe a API de estimativas a parceiros e a 99 não tem API pública.
- Não há trânsito em tempo real gratuito: o congestionamento vem do perfil histórico por horário e da fração da
  rota na Amaral Peixoto, e os acidentes só aparecem depois que o preço observado os mostra.
- A faixa não inclui o erro da previsão de chuva (ver "Chuva prevista" acima).
- A localização automática erra quilômetros (IP) e só seria precisa por Wi-Fi onde o BeaconDB tem varredura; use
  o mapa ou os lugares já usados para o ponto exato.
- O OSRM público e o espelho da FOSSGIS não têm garantia de disponibilidade. Rotas já consultadas ficam em cache
  no banco.
