# Tarifa Dinâmica

App desktop em Python (modo escuro) que mostra, para uma corrida por aplicativo, o preço e o tempo de viagem
agora e a previsão das próximas horas em três linhas no mesmo eixo de tempo: preço (p10, p50, p90), chuva
prevista (chance por hora e mm/h) e tempo de viagem previsto. Um seletor no topo escolhe entre **Uber** e **99**,
cada uma com a sua tarifa e a sua dinâmica. Janela ajustável de 1 a 12 h, passos de 10 min e acompanhamento em
tempo real com alerta sonoro quando o preço se afasta 10% do valor da primeira consulta. A versão Android, com o
mesmo modelo e os mesmos números, fica em `../Mobile`.

> **Os preços são simulados.** Não existe API pública gratuita de preços da Uber ou da 99 — a Uber restringe a
> API de estimativas a parceiros e a 99 não tem API pública —, então o preço vem de um oráculo que simula o
> mercado (`Oracle/`) sobre a tabela de tarifas de cada aplicativo. **Para o valor bater com o que o app cobra de
> verdade na sua cidade, informe um preço real** no campo *preço real no app*: a tarifa daquele aplicativo passa
> a ser ajustada a ele. O resto é dado real e aberto: endereços (OpenStreetMap via Photon e Nominatim), rotas
> (OSRM), clima (Open-Meteo) e localização (Wi-Fi pelo BeaconDB e IP).

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

1. Escolha o aplicativo no seletor **Uber | 99**. Trocar recalcula o preço e o gráfico na hora, sem consultar a
   rede de novo: a rota, o clima e o modelo são os mesmos, só a tarifa e a dinâmica mudam.
2. Digite origem e destino. A lista de sugestões aparece enquanto você digita, com aviso de carregando.
3. O botão ◎ de cada campo abre a janela "Onde você está?": escolha um lugar já usado, em um clique, ou marque
   o ponto exato no mapa. O mapa abre na estimativa de localização; quando ela é grosseira (por IP, quilômetros de
   erro), abre no lugar já usado mais recente dentro dessa área, e sem estimativa abre no último lugar usado ou na
   região. Arraste para mover, use a roda do mouse ou +/− para o zoom e clique no ponto exato.
4. **Calcular preço** mostra o preço e o tempo de viagem de agora, o resumo da rota e as três linhas do gráfico:
   - **Preço da corrida** (a maior): preço previsto com a faixa p10–p90, o preço observado agora e o pico da janela;
   - **Chuva prevista**: a chance de chuva hora a hora numa faixa colorida e, logo abaixo, a intensidade em mm/h;
   - **Tempo de viagem previsto**: quanto tempo leva até o destino saindo em cada horário, em minutos (ou horas nas
     rotas longas), com a faixa m10–m90 e as linhas sem trânsito, moderado e intenso.
   Passando o mouse, uma cruzeta mostra preço, tempo de viagem, hora de chegada, minutos a mais pelo trânsito e
   chuva daquele horário.
5. O controle no topo do gráfico escolhe a janela de 1 a 12 h. A série inteira já vem calculada, então trocar a
   janela não recalcula nada nem muda a precisão.
6. O **tempo real** fica sempre ligado, sem botão: a cada 30 s o preço da rota na tela é observado de novo:
   - o preço fica **azul com ▼** quando está abaixo do valor da primeira consulta da rota e **vermelho com ▲**
     quando está acima, sempre com a porcentagem acumulada;
   - a cada 10% de afastamento toca um alerta: arpejo ascendente quando cai, grave descendente quando sobe;
   - o alerta não se repete no mesmo patamar; toca de novo quando passa o próximo múltiplo de 10%.
7. **Calibrar com o preço real.** Abra a Uber ou a 99 na mesma rota, veja o valor que o app cobra agora, digite-o
   em *preço real no app* e clique em **Calibrar**: a tarifa daquele aplicativo passa a bater com ele. Quanto mais
   preços você informar, em rotas de tamanhos diferentes, mais a tabela inteira converge. **0** apaga a calibração
   e volta à tabela publicada. A linha sob o campo diz sempre qual tarifa está em uso.

## Estrutura

```
index.py            ponto de entrada: log, worker e janela
bootstrap.py        gerador do histórico sintético do primeiro boot (14 rotas reais)
test.py             validação de relógio, serviços, distâncias, oráculo, tarifas, cenários, precisão, rotas fora do treino, acidentes, worker e janela
Api/index.py        Photon, Nominatim, OSRM (com espelho de reserva), Open-Meteo, BeaconDB e provedores de IP, com fila por servidor e retentativa
Database/index.py   SQLite em WAL: Routes, Prices (estado do mercado), Forecasts, Fares (preços reais informados) e Metrics
Engine/index.py     rota, clima, preço e tempo observados agora, série ancorada e calibração da tarifa
Model/index.py      LightGBM quantílico por aplicativo, âncora por horizonte com corte de choque e calibração conformal
Oracle/index.py     tarifa de cada aplicativo e mercado simulado que define o preço
Worker/index.py     oráculo a cada 10 min, consolidação das previsões dos dois aplicativos e retreino
Interface/          janela (index.py), gráfico (Chart/), campo com autocomplete (Search/) e escolha do ponto no mapa (Locator/)
Utils/              constantes, relógio com feriados e alertas sonoros
```

## Arquitetura

**Tarifa de cada aplicativo.** O seletor troca entre Uber e 99, e cada uma tem a sua tabela: bandeirada, valor
por km, valor por minuto, taxa fixa, tarifa mínima, sensibilidade à dinâmica e teto dela. A tabela da Uber
reproduz os R$ 54,85 medidos na rota de referência em dia útil fora do pico; a da 99 fica cerca de 10% abaixo
nesse mesmo horário e mais ainda no pico, porque a dinâmica dela reage menos ao excesso de demanda — é o que a
comparação pública mostra (a 99 tem o menor preço base em 19 das 27 capitais e o menor preço por minuto em 21).
A referência publicada da UberX na capital do Rio em 2026 é bandeirada R$ 2,30, R$ 1,55/km, R$ 0,32/min, mínima
R$ 10,50 e taxa de reserva R$ 0,75; a 99 abandonou a tabela fixa e passou a precificar por região e demanda.
O **tempo de viagem não muda com o aplicativo**: o trânsito é do mercado, e os dois mostram o mesmo m50.

**Calibração com o preço real.** Como a tarifa varia por cidade e muda sem aviso, o campo *preço real no app*
ancora a tabela no que você vê de verdade: digite o valor que a Uber ou a 99 mostra agora para a rota na tela e
clique em **Calibrar**. O app guarda a observação com o estado do mercado daquele instante (distância, minutos e
multiplicador da dinâmica) e reajusta os coeficientes por mínimos quadrados, com uma crista que segura a forma da
tabela publicada: com um preço só, o nível inteiro se desloca e aquela rota passa a bater exatamente; com três ou
mais, em rotas de tamanhos diferentes, a forma também se ajusta e o acerto passa a valer para rotas que você não
informou (com seis preços 12% acima da tabela, o erro residual fica abaixo de 0,7% em todas elas). Preço abaixo da
tarifa mínima calibra o piso. Nenhuma correção passa do dobro nem da metade da tabela publicada, para um valor
digitado errado não destruir a tarifa, e **0** apaga a calibração daquele aplicativo. Os preços informados ficam
na tabela `Fares`, que sobrevive à recriação do banco, e valem para qualquer rota daquele aplicativo.

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

**Localização atual.** Sem GPS, nenhuma fonte gratuita acerta a rua, e não existe serviço que resolva isso: o
Mapbox e os concorrentes não vendem localização por IP, eles usam o GPS do próprio aparelho. O que dá para fazer é
perguntar a várias fontes ao mesmo tempo e ficar com a mais precisa: o app dispara em paralelo o BeaconDB (que usa
as redes Wi-Fi visíveis, lidas pelo `nmcli`, e devolve a precisão em metros) e três provedores de IP sem cadastro
(geojs, ipwho.is e ipinfo), tira a mediana das coordenadas deles e usa a dispersão entre elas como erro declarado.
Onde o BeaconDB tem varredura de Wi-Fi ele ganha, com erro no nível da rua; onde não tem, ele cai para IP e
declara ~25 km, enquanto a mediana dos três provedores fica em ~10 km — e é ela que vale. Tudo isso em cerca de
1 s, porque as consultas são simultâneas. Por isso o ◎ não preenche sozinho: ele abre a janela "Onde você está?", com os lugares
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

1. Observa o estado do mercado do oráculo nas 5 rotas usadas mais recentemente; as sintéticas do bootstrap já têm
   um ano de histórico e ficam de fora.
2. Guarda a previsão das próximas 12 h de cada uma, de preço e de tempo, **nos dois aplicativos**.
3. Consolida as previsões passadas contra o que foi observado: erro médio, cobertura das faixas e perda pinball
   das últimas 24 h, para preço e tempo, na tabela `Metrics` e na barra de status. A barra só mostra a taxa de
   acerto a partir de 50 previsões consolidadas e, antes disso, mostra a contagem (com 3 previsões a faixa chegou a
   marcar 33%).
4. Retreina a cada 1 h com todo o histórico acumulado (~1,5 min: as 736 mil observações do mercado viram 1,47
   milhão de linhas de treino, uma por aplicativo).

O LightGBM libera o GIL durante o treino, então a janela continua responsiva. Um erro inesperado vai para o log
e o ciclo seguinte tenta de novo. A cobertura da barra de status mede poucas dezenas de previsões correlacionadas:
durante um acidente simulado ela cai por algumas dezenas de minutos (hoje, às 11:50, marcou 46% no preço) e volta.

**Modelo.** Três camadas, cada uma resolvendo um problema diferente:

- **Dois alvos, mesma máquina.** O modelo prevê o preço e o tempo de viagem com a mesma arquitetura: cada um
  aprende o multiplicador sobre a sua base (tarifa sem dinâmica para o preço, tempo sem trânsito para a viagem),
  porque árvores não extrapolam distância mas o multiplicador vale para rotas de qualquer tamanho.
- **Estrutura (LightGBM quantílico).** Atributos: hora local contínua, dia da semana **com feriado valendo como
  domingo**, distância, duração livre, fração no corredor, chuva e **o aplicativo**, como categoria. Cada
  observação do mercado vira uma linha de treino por aplicativo, com o preço e a base daquela tarifa, então o
  modelo aprende de uma vez a dinâmica mais forte da Uber e a mais contida da 99 sem dois modelos separados.
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

**Oráculo.** O banco guarda o **estado do mercado** de cada instante — excesso de demanda, ruído da oferta e
minutos de viagem —, e não o preço: o preço de cada aplicativo sai desse estado com a tarifa dele, então trocar de
aplicativo ou calibrar a tarifa não invalida um ano de histórico nem exige regerar nada. Demanda por picos
gaussianos na semana local, com feriado seguindo o perfil de domingo; atraso de
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

Tudo abaixo sai do `test.py` (127 verificações, todas passando), contra o oráculo, em dias e rotas fora do treino
e **nos dois aplicativos**.

**Tarifa e calibração.** Ao longo de 120 dias na rota de referência, a 99 sai de 9,6% a 15,9% abaixo da Uber
(média 10,7%), mais barata em todo instante, e a diferença cresce com a dinâmica (correlação −0,92 entre o preço
da Uber e a razão entre os dois); fora do pico a mediana da 99 fica em 0,900 da Uber. O tempo de viagem é
idêntico nos dois. Na calibração, seis preços informados 12% acima da tabela publicada — em rotas de 1 a 60 km,
com e sem dinâmica, inclusive abaixo da tarifa mínima — deixam erro residual **abaixo de 0,7%** em todas elas;
com **um** preço só, a rota informada passa a bater **exatamente** (na janela real, pedido R$ 40,19 e mostrado
R$ 40,19 no desktop; pedido R$ 44,90 e mostrado R$ 44,90 no Android); um preço absurdo fica preso no limite do
dobro da tabela e **0** devolve a tabela publicada.

**Precisão por horizonte** (8 dias futuros, as 14 rotas do bootstrap, âncoras a cada 3 h):

| Preço | agora | 10–30 min | 40–60 min | 1–3 h | 3–6 h | 6–12 h |
|---|---|---|---|---|---|---|
| Uber, sem âncora | 2,5% | 2,3% | 2,3% | 2,4% | 2,4% | 2,4% |
| Uber, rota rastreada | **0%** | 2,1% | 2,1% | 2,2% | 2,2% | 2,3% |
| Uber, rota nova (1 observação) | **0%** | 2,3% | 2,3% | 2,3% | 2,3% | 2,4% |
| 99, sem âncora | 2,6% | 2,3% | 2,4% | 2,4% | 2,4% | 2,4% |
| 99, rota rastreada | **0%** | 2,2% | 2,2% | 2,3% | 2,3% | 2,3% |
| 99, rota nova (1 observação) | **0%** | 2,3% | 2,3% | 2,4% | 2,4% | 2,4% |

| Tempo de viagem (o mesmo nos dois) | agora | 10–30 min | 40–60 min | 1–3 h | 3–6 h | 6–12 h |
|---|---|---|---|---|---|---|
| Sem âncora | 3,7% | 3,5% | 3,5% | 3,6% | 3,6% | 3,6% |
| Rota rastreada | **0%** | 3,3% | 3,3% | 3,4% | 3,5% | 3,5% |
| Rota nova (1 observação) | **0%** | 3,5% | 3,5% | 3,5% | 3,6% | 3,6% |

A cobertura das faixas fica entre 81% e 82% em todos os horizontes, regimes, alvos e aplicativos. O piso de erro é
a variação do próprio mercado simulado — cerca de 2,0% no preço e 3,2% no tempo — então as duas previsões estão
perto do limite do que é previsível.

**Rotas fora do treino** (6 dias, âncoras a cada 3 h, rota rastreada; os números da 99 não diferem dos da Uber em
mais de 0,2 ponto):

| Rota | Erro preço | Erro tempo | Viés tempo | Faixa preço | Faixa tempo |
|---|---|---|---|---|---|
| 2 km, sem corredor | 2,1% | 3,3% | +0,4% | 81% | 80% |
| 9 km, 30% no corredor | 2,1% | 3,6% | +0,1% | 82% | 77% |
| 30 km, sem corredor | 2,2% | 3,5% | +0,6% | 79% | 78% |
| 30 km, 90% no corredor | 2,7% | 4,1% | −0,3% | 77% | 80% |
| 100 km, sem corredor | 2,1% | 3,1% | −0,3% | 82% | 83% |
| 300 km, 30% no corredor | 2,3% | 3,3% | −0,2% | 77% | 81% |

**Acidentes no corredor** (60 dias, 3 rotas com mais de 55% na RJ-106, consultas feitas com o acidente em curso,
nos dois aplicativos):

| Horizonte | Erro tempo | Viés tempo | Faixa tempo | Erro preço | Viés preço | Faixa preço |
|---|---|---|---|---|---|---|
| primeira hora | 4,8% | −2,4% | 68% | 2,9% | −1,3% | 69% |
| 1–3 h | 3,9% | +0,4% | 78% | 2,6% | +0,5% | 76% |
| 3–12 h | 3,7% | +1,1% | 80% | 2,6% | +0,9% | 76% |

O começo de um acidente é imprevisível; a âncora com corte segue o choque e o dissipa. Na comparação nos mesmos
dados com a âncora anterior, a primeira hora tinha viés de −6,4% no tempo e faixa cobrindo 45%, e o resto do dia
ficava 1,7% caro porque o acidente contaminava o nível do dia.

**Chuva prevista.** A faixa assume que a previsão de chuva acerta. Em 90 dias de previsões reais do Open-Meteo
(previsão da véspera contra a mais recente), nos slots com chuva a faixa de preço cobre 50% e o erro do preço
dobra (2,2% → 4,2%); nos slots secos nada muda. Quando a chance de chuva está alta, trate o p90 como otimista.

**Cenários de horário e chuva** (previsão de 1 h à frente contra o que o oráculo cobra, 8 cenários × 2
aplicativos): erro médio de 2,13% no preço e 2,09% no tempo, com o valor real dentro da faixa em 7 dos 8 cenários
de cada aplicativo — madrugada, meio da manhã, rush de quarta, rush de sexta seco e com chuva forte, domingo à
tarde, feriado de 7 de setembro e segunda comum.

**Distância e tempo:** a rota de teste dá 34,1 km e 42,7 min, confirmados pelo espelho da FOSSGIS, que também
assume a consulta quando o servidor principal está fora do ar; a distância rodoviária fica entre 1 e 3 vezes a
linha reta; as velocidades médias das rotas testadas vão de 28 a 80 km/h; ponto no mar e ilha sem ligação
rodoviária são recusados.

**Qualquer rota**, nos dois aplicativos, com fuso certo, série de 73 pontos, faixas abertas e a 99 sempre abaixo
da Uber com o mesmo tempo de viagem:

| Rota | km | sem trânsito | Uber | 99 |
|---|---|---|---|---|
| Copacabana → Ipanema (RJ) | 5,1 | 6 min | R$ 11,61 | R$ 9,43 |
| Paulista → Ibirapuera (SP) | 5,2 | 11 min | R$ 12,44 | R$ 10,35 |
| Rio → Niterói (ponte) | 16,3 | 17 min | R$ 28,26 | R$ 24,58 |
| Rio → São Paulo (431 km) | 431,7 | 5h26 | R$ 627,43 | R$ 569,46 |
| Manaus centro → aeroporto (fuso `America/Manaus`) | 15,2 | 18 min | R$ 25,62 | R$ 22,52 |

## Limitações

- **O preço não é o da Uber nem o da 99 de verdade, e nenhum código resolve isso sozinho.** Não existe API
  pública gratuita de preços (a Uber restringe a de estimativas a parceiros, a 99 não tem), então o app simula o
  mercado sobre a tabela de tarifas de cada aplicativo. O caminho para ele bater com o app é a calibração:
  informe o preço real e a tarifa se ajusta: com um preço, a rota informada passa a bater exatamente; com três ou
  mais, em rotas de tamanhos diferentes, a tabela inteira converge. O que a calibração não alcança é a **dinâmica
  daquele instante**: se a Uber estiver aplicando um multiplicador que o oráculo não previu, o valor mostrado
  difere até você informar um preço novo.
- Não há trânsito em tempo real gratuito: o congestionamento vem do perfil histórico por horário e da fração da
  rota na Amaral Peixoto, e os acidentes só aparecem depois que o preço observado os mostra.
- A faixa não inclui o erro da previsão de chuva (ver "Chuva prevista" acima).
- A localização automática erra quilômetros: onde o BeaconDB conhece as redes Wi-Fi ela chega ao nível da rua,
  onde não conhece vale a mediana dos provedores de IP (~10 km nesta conexão). Use o mapa ou os lugares já usados
  para o ponto exato; no Android o GPS resolve.
- O OSRM público e o espelho da FOSSGIS não têm garantia de disponibilidade. Rotas já consultadas ficam em cache
  no banco.
