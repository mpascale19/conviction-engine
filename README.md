# Conviction Engine — pipeline dei dati

Raccoglie da sola i dati pubblici di un titolo, applica le regole dello score e
scrive `site/data.json`. Gira ogni sera su GitHub Actions e pubblica il sito su
GitHub Pages: il link resta lo stesso e i numeri si aggiornano da soli.

Il principio è quello del progetto: **l'LLM estrae, non decide**. Il modello
legge le trascrizioni e ne tira fuori campi strutturati; il punteggio lo calcola
`pipeline/score.py`, che puoi leggere riga per riga.

## Che cosa è automatico e che cosa no

| Dato | Fonte | Automatico |
|---|---|---|
| Prezzi e volumi | Yahoo Finance, con stooq.com come riserva | sì, senza chiave |
| Operazioni degli insider (Form 4) | SEC EDGAR | sì, senza chiave |
| Rendimento del settore | Yahoo / stooq (ETF di settore) | sì |
| Tono e guidance dalle call | Alpha Vantage (chiave gratuita) + estrazione in una conversazione con Claude, oppure un LLM via API | sì, se metti i testi in `input/transcripts/` **e** nei segreti del repository c'è `ANTHROPIC_API_KEY`; finora le 24 estrazioni sono state fatte a mano, con lo stesso schema |
| Short interest | FINRA, file pubblici (riserva: export Bloomberg) | sì, senza chiave |
| Concorrenti | SEC EDGAR | sì, quando aggiungi i loro ticker |
| Fatti rilevanti (8-K) | SEC EDGAR | sì, senza chiave e senza richieste in più |

Quello che manca non blocca niente: il peso del sotto-punteggio assente viene
ridistribuito sugli altri, lo score esce lo stesso e `data.json` dice che cosa
mancava e con quale confidenza. La parte pubblica (SEC, prezzi e short interest
di FINRA) copre l'80% dei pesi; con le trascrizioni si arriva al 100%.

## Manutenzione

### Il gesto normale

**Actions -> Aggiorna i dati -> Run workflow.** Due o tre minuti e il sito è
aggiornato. Parte da solo anche quando carichi file in `pipeline/`, `input/`,
`config.json` o `site/`: in quel caso non devi premere niente.

### Cosa invecchia, e ogni quanto

| Dato | Come si aggiorna | Ogni quanto serve |
|---|---|---|
| Prezzi di chiusura | da solo, Yahoo | a ogni giro |
| Operazioni Form 4 | da solo, SEC EDGAR | a ogni giro, ogni ora |
| Azioni in circolazione | da solo, SEC | a ogni giro |
| Fatti rilevanti 8-K | da solo, SEC EDGAR | a ogni giro |
| Short interest | da solo, FINRA | due volte al mese, quando FINRA pubblica |
| Trascrizioni delle call | da sole, Alpha Vantage (25 al giorno); i fatti a mano | dopo ogni stagione degli utili |

Lo short interest lo pubblica FINRA due volte al mese, con la data di
regolamento del 15 e quella di fine mese, circa sette giorni di Borsa dopo. La
pipeline lo scarica da sola (vedi la sezione "Short interest"): non c'è più
niente da esportare a mano.

Le trascrizioni servono solo dopo una earnings call, e solo per le aziende che
segui davvero. Il tono usa le ultime quattro call: se ne salti una, il
sotto-punteggio continua a funzionare con quelle che ci sono.

### Aggiungere un'azienda

Si mette il ticker nella lista giusta dentro `config.json` e si lancia il
workflow. Se nel log compare un errore di verifica del CIK, vuol dire che la
mappa della SEC sbaglia su quel ticker: si aggiunge il numero a mano nel campo
`cik` dello stesso file. Prima di aggiungerla conviene controllare tre cose:
che non sia in corso una fusione, che non abbia fatto uno scorporo negli ultimi
cinque anni, e che non abbia cambiato sede o capogruppo (in quel caso il CIK
vecchio va in `cik_predecessors`).

### Farlo partire da solo

In `.github/workflows/update.yml` ci sono due righe commentate sotto
`schedule`. Togliendo il cancelletto l'aggiornamento parte ogni giorno feriale
alle 22:30 UTC, dopo la chiusura di New York. Da sapere: GitHub sospende le
pianificazioni nei repository fermi da 60 giorni, avvisando per email; basta
riattivarle dalla scheda Actions.

### Se qualcosa si rompe

| Sintomo | Causa quasi certa |
|---|---|
| 403 dalla SEC | il secret `SEC_UA` non è "Nome Cognome email@dominio" |
| un ticker salta con errore sul CIK | la mappa SEC sbaglia: metti il CIK in `config.json` |
| un'azienda resta senza prezzi | Yahoo e stooq hanno entrambi rifiutato; l'errore è scritto in `errors[]` e nessun punteggio viene inventato |
| il workflow si ferma sui test | un errore nel codice: i test girano **prima** di scaricare qualsiasi cosa, quindi i dati pubblicati non si rovinano mai |
| un file che hai caricato torna indietro da solo | era il salvataggio automatico: usava `git reset --soft`, che teneva nell'indice la versione dei file vista all'avvio del giro e quindi annullava i caricamenti fatti nel frattempo. Corretto il 20/11 con un reset normale; se dovesse ricapitare, controlla che in `update.yml` non sia tornato `--soft` |
| il sito mostra dati vecchi | il secondo riquadro del workflow (`pubblica`) non è ancora verde |
| un giro fallisce sul salvataggio | due giri ravvicinati: il più recente ha già scritto. Non si perde niente, i dati li rigenera il giro dopo |

### Perché i dati non si fondono mai

I file in `site/` sono generati per intero a ogni giro: la versione appena
calcolata è sempre quella buona. Per questo il workflow non fa `git pull
--rebase` ma riparte da `origin/main` e riscrive sopra. Un rebase proverebbe a
fondere due versioni degli stessi 41 file, che differiscono su ogni riga per
l'orario, e finirebbe in conflitto su tutti.

Allo stesso modo, se parte un giro mentre un altro è in corso, il vecchio viene
fermato (`cancel-in-progress`): il nuovo rifà tutto comunque.

### Il controllo dopo ogni giro

Tre cose, trenta secondi: la data in alto nel sito deve essere quella di adesso,
il riquadro rosso degli errori non deve comparire, e nel log del workflow ogni
riga `CIK` deve avere accanto il nome giusto dell'azienda.

## Le trascrizioni delle call

### Da dove arrivano: Alpha Vantage, senza Bloomberg

Dal 22 novembre 2026 le trascrizioni non servono più da Bloomberg. Un workflow
a sé, **Scarica trascrizioni** (`.github/workflows/trascrizioni.yml`, codice in
`pipeline/trascrizioni.py`), le chiede ad Alpha Vantage una volta al giorno, o
quando lo si preme a mano. La chiave gratuita dà 25 richieste al giorno, e il
workflow le usa nell'ordine in cui servono di più:

1. prima le aziende con un cluster di acquisti, poi quelle con più compratori
   negli ultimi 90 giorni, poi le altre per punteggio;
2. per ogni azienda prima l'ultima call, poi la penultima: tutte hanno la più
   recente prima che qualcuna abbia la seconda.

Le date delle call sono quelle degli 8-K con item 2.02 (i risultati), che le
schede hanno già. Alpha Vantage vuole il trimestre **fiscale**: la call di
Nvidia di novembre 2026 è il `2027Q3`, perché il suo anno fiscale finisce a
gennaio. Il mese di chiusura lo dà la SEC (`fiscalYearEnd`). Una call che
resta vuota per due giorni non si chiede più, e il riepilogo dice che cosa ha
risposto Alpha Vantage.

Non c'è riserva sul trimestre di calendario: c'era, e il primo giro vero ha
portato cinque call sbagliate. Per TJX, Salesforce, Sysco, Atlassian e Clorox
il trimestre fiscale non ha risposto, e "2026Q3" per Alpha Vantage è il terzo
trimestre dell'anno *fiscale* 2026: la call di TJX arrivata era del novembre
2025, quella di Salesforce dell'agosto 2025. Ora ogni trascrizione si controlla
(`call_matches`): nelle prime righe deve comparire l'anno del trimestre chiesto,
altrimenti si scarta. Le cinque call sbagliate non sono state estratte.

Ogni call diventa un file Word, `TICKER_TRIMESTRE.docx`, in un pacchetto che si
scarica dalla pagina del giro (sezione *Artifacts*) e resta lì tre giorni, con
un `riepilogo.txt` di che cosa è arrivato e che cosa no. **Non entra nel
repository né sul sito**: sono testi protetti, e il repository è pubblico.

Il passo dopo è a mano, e per questo è gratis: i file Word si danno a Claude in
una conversazione, Claude ne estrae i fatti con lo schema qui sotto, e il JSON
che ne esce si carica in `input/extracted/`. Le 24 estrazioni di prima sono
state fatte nello stesso modo. Chi ha una chiave API di un modello può invece
mettere i testi in `input/transcripts/` e lasciare che lo faccia `extract.py`.

Per farlo funzionare serve un segreto del repository, `ALPHAVANTAGE_KEY`: la
chiave gratuita si chiede su alphavantage.co con un indirizzo email.

### Il testo e i fatti

Il testo delle earnings call è materiale sotto licenza: **non va nel
repository pubblico**. Per questo la pipeline legge
due cartelle diverse.

```
input/transcripts/INTC_2026Q3.txt    il testo: resta sul tuo computer (.gitignore)
input/extracted/INTC_2026Q3.json     i fatti estratti: questi sì, nel repo
```

Il nome del file decide a quale azienda appartiene la call: il ticker sta
all'inizio, prima del primo trattino o underscore. `INTC_2026Q3.txt` finisce su
Intel, `transcript (3).txt` su nessuno. Se un export contiene più call
accodate, va diviso in un file per trimestre.

**Che cosa c'è dentro oggi.** 24 call estratte su 5 aziende: Intel (8 trimestri),
Oracle, NVIDIA, Bank of America e Coca-Cola (4 ciascuna). Per ogni azienda si
tengono le quattro call più recenti, che sono quelle che il punteggio usa
davvero: la media del tono sulle ultime quattro e la guidance dell'ultima. Le
call più vecchie restano archiviate in `input/transcripts/` e si possono
estrarre quando servono.

**Lo short interest** copre 36 aziende su 40. Mancano PG, UNH, WMT e XOM: per
loro il punteggio redistribuisce il peso sulle altre componenti invece di
inventare un numero, e la scheda lo dichiara.

Da `input/extracted/` escono tono, guidance e direttezza delle risposte senza
nessuna chiamata al modello: serve a far girare la pipeline senza chiave API e a
tenere il testo fuori dal repo. Se invece metti un `.txt` senza il JSON
corrispondente e hai impostato `ANTHROPIC_API_KEY`, l'estrazione parte da sola e
produce la stessa forma.

Anche le estrazioni sono bilingui, con la stessa forma `{"it": ..., "en": ...}`
che usa il resto della pipeline. Sono tradotti tutti i testi che il sito mostra:
la nota sul tono di ogni call, la guidance e le domande dell'**ultima** call di
ogni azienda, e **tutti** i cambi di linguaggio. La guidance delle call
precedenti resta in italiano perché nessuna pagina la mostra; se un domani la
mostrerai, va tradotta. Dove manca l'inglese il sito ripiega sull'italiano senza
rompersi.

Il sotto-punteggio **Guidance e tono** usa la media delle ultime quattro call
estratte di quella azienda. Prima di questa parte la sua copertura era zero e il
peso (20%) veniva ridistribuito sugli altri.

## La divergenza, senza comprare le stime degli analisti

Il consenso degli analisti è un prodotto a pagamento: la SEC non lo pubblica,
perché non è un dato dell'azienda. Ma la tesi del progetto -- *gli insider
comprano mentre le aspettative peggiorano* -- non ha bisogno degli analisti:
l'azienda stessa dice a ogni call se alza o abbassa la propria guidance, e
quella frase è pubblica.

Ogni voce di guidance estratta porta una `direction`: `alzata`, `ridotta`,
`confermata`, `prima` (data per la prima volta). Il sotto-punteggio
**Guidance e tono** parte dal tono medio delle ultime quattro call e lo corregge
di 2,5 punti per ogni voce alzata o ridotta nell'ultima call, con un massimo di
dieci punti in più o in meno.

Quando l'azienda taglia la guidance *e* un insider ha comprato negli ultimi 90
giorni, il sito lo scrive: "gli insider hanno comprato mentre l'azienda tagliava
la guidance". È il segnale che il progetto esiste per trovare, e costa zero.

## Short interest

Dal 22 novembre 2026 lo short interest arriva dai **file pubblici di FINRA**
(`pipeline/finra.py`), per tutte le duecento aziende e per cinque anni. FINRA
pubblica un file per ogni data di regolamento, il 15 e la fine del mese:

```
https://cdn.finra.org/equity/otcmarket/biweekly/shrtAAAAMMGG.csv
```

Nonostante "otcmarket" nell'indirizzo è il file consolidato, con i titoli di
NYSE, Nasdaq e delle altre borse; i campi sono separati da `|` e non serve
nessuna chiave. Ogni file pesa circa 3 MB e ha più di ventimila righe: in cache
(`cache/finra/`) resta un file piccolo per rilevazione, con le sole righe delle
aziende di `config.json`. Le 120 rilevazioni di cinque anni si scaricano a rate,
60 richieste per giro al massimo (`finra_files_per_run`), dalla più recente.

Due dettagli del calendario. Il nome del file è la data di regolamento, che
slitta indietro quando il 15 o la fine del mese cadono di sabato, di domenica o
in un giorno di festa: la pipeline prova i giorni feriali andando indietro. E un
file esce circa sette giorni di Borsa dopo: una rilevazione più recente di tre
settimane che non si trova si riprova al giro dopo, una più vecchia si segna in
cache come inesistente. Questo però solo se nello stesso giro un altro file è
arrivato: se FINRA non risponde a niente è un guasto, e cinque anni di "nessun
dato" sarebbero un falso. Se qualcosa va storto, `index.json` lo dice in
`short_interest.error` (e in `short_interest.head` c'è l'intestazione del file
ricevuto), così si legge senza aprire il log.

La pipeline usa il campo `currentShortPositionQuantity` (azioni allo scoperto),
`averageDailyVolumeQuantity` (volume medio) e `daysToCoverQuantity` (giorni per
ricoprire). Prima si usavano gli export manuali da Bloomberg (`SI <GO>`), un
file per azienda:

```
input/market/INTC_si.csv
date,short_interest,close,avg_volume,days_to_cover
```

Restano come riserva: un'azienda li usa solo finché FINRA non ha almeno otto
sue rilevazioni. È lo stesso dato, ma tutte le aziende vanno misurate con la
stessa fonte appena si può. La scheda dice quale fonte ha usato (`si_source`).
Passando a FINRA lo short interest delle 36 aziende che avevano l'export può
spostarsi un poco, perché la data e l'arrotondamento non coincidono sempre.

La prova all'indietro della voce Mercato usa tutti e cinque gli anni (in memoria
durante il giro, come i prezzi lunghi); prima aveva solo i due anni degli
export, sei blocchi indipendenti in tutto.

L'export non contiene la percentuale sul flottante, quindi il sotto-punteggio
**Mercato** non la usa: misura lo short interest contro la storia del titolo
stesso (scostamento dalla mediana dell'ultimo anno, direzione negli ultimi tre
mesi, giorni per ricoprire rispetto alla loro mediana). Così un'azienda grande
e una piccola restano confrontabili, e un numero non pesa per il fatto di essere
grande. Un test lo verifica: la stessa serie moltiplicata per dieci dà lo
stesso punteggio.

### Percentuale sul circolante

Le azioni in circolazione non arrivano da FINRA: stanno in copertina a ogni
10-Q e 10-K, e la SEC le espone in un endpoint pubblico
(`companyconcept/.../dei/EntityCommonStockSharesOutstanding`). La pipeline le
scarica da sola per tutte le aziende, insieme ai Form 4.

Attenzione a come si chiama il numero: quello che esce è lo short interest in
**percentuale delle azioni in circolazione**, non del flottante. Il flottante
esclude le azioni vincolate e quelle in mano a insider e grandi soci, quindi la
percentuale vera sul flottante è un po' più alta. Per le grandi società la
differenza è piccola, ma il sito scrive "azioni in circolazione" e non
"flottante", perché è quello che il dato dice davvero.

### Il confronto con i simili

Quando più aziende dello stesso universo hanno lo short interest, il
sotto-punteggio Mercato aggiunge un confronto: la percentuale sulle azioni in
circolazione di questa azienda contro la **mediana del suo gruppo**. Essere
shortati molto più dei simili toglie punti, molto meno li aggiunge, con un
tetto di otto. Servono almeno tre aziende confrontabili, altrimenti il confronto
non si fa.

È la differenza fra "è shortata più del solito" (confronto con la propria
storia) e "è shortata più delle altre" (confronto con il settore). Le due cose
insieme dicono molto di più di ciascuna da sola.

Lo short interest nasce da FINRA e dalle borse, non è un prodotto proprietario
Bloomberg: per questo i numeri stanno nel repo, e da novembre 2026 arrivano
direttamente da FINRA. Le revisioni delle stime e il
consenso EPS, quando arriveranno, seguiranno la regola delle trascrizioni.

## Da ticker a CIK: il controllo

La SEC identifica le aziende con un numero, il CIK, e pubblica una mappa
`ticker -> CIK`. Quella mappa ogni tanto sbaglia: il 16/11/2026 dava `ADI`
(Analog Devices) come Eli Lilly, e senza controlli il progetto avrebbe
attribuito ad Analog Devices le operazioni degli insider di un'altra azienda.

Ora un CIK viene accettato solo se il JSON dei depositi di quel CIK elenca
davvero quel ticker (`sources.confirms`). Se non torna, la pipeline prova la
ricerca di EDGAR e poi il numero scritto a mano in `config.json`, campo `cik`;
se non torna niente, l'azienda viene saltata con un errore che dice che cosa ha
trovato al posto suo. Il controllo non costa richieste in più: quel JSON serve
comunque subito dopo per scaricare i Form 4.

## Italiano e inglese

Il sito ha un interruttore **IT / EN** in alto a destra e si apre nella lingua
del browser di chi arriva (inglese se non è italiano). La scelta resta salvata
in quel browser.

Non è tradotta solo l'interfaccia: anche le frasi che la pipeline genera
("Cluster di 3 acquirenti distinti entro 30 giorni.") escono nelle due lingue.
In `pipeline/rules.py` c'è una funzione di tre righe:

```python
def L(it, en):
    return {"it": it, "en": en}
```

Ogni testo destinato al sito passa di lì e nel JSON diventa
`{"it": "…", "en": "…"}`; la pagina sceglie la lingua al momento di mostrarlo.
Se aggiungi una frase nuova in `score.py` o in `build.py`, scrivila dentro `L(...)`:
il test `tests/test_offline.py` controlla che ogni testo generato esista in
entrambe le lingue e fallisce se te ne dimentichi una.

Per i nomi degli universi la traduzione sta in `config.json`, nel campo
`nome_en` accanto a `nome`.

## I fatti rilevanti: l'8-K al posto delle notizie

La voce che valeva il 10% si chiamava *News* e non è mai stata alimentata, perché
una fonte di notizie gratuita, stabile e verificabile non esiste. Al suo posto
ora c'è una fonte migliore e primaria: il **modulo 8-K**, con cui un'azienda
americana è obbligata a dichiarare alla SEC un fatto rilevante entro quattro
giorni lavorativi.

Il vantaggio non è solo che è gratis. Un articolo va interpretato; un 8-K porta
dei **codici item fissati dalla SEC**, quindi non c'è niente da interpretare:
`4.02` vuol dire sempre che i conti già pubblicati non sono più affidabili,
`1.03` vuol dire fallimento, `4.01` vuol dire che è cambiato il revisore. È
esattamente il principio del progetto — i fatti si estraggono, non si giudicano —
applicato a una fonte nuova.

I pesi che diamo ai codici stanno in `EVENT_W` dentro `pipeline/score.py`, in
chiaro come tutto il resto:

| Codice | Fatto | Peso |
|---|---|---|
| 1.03 | fallimento o amministrazione controllata | −30 |
| 4.02 | i conti già pubblicati non sono più affidabili | −22 |
| 3.01 | avviso di esclusione dal listino | −15 |
| 2.06 | svalutazione rilevante | −10 |
| 2.04 | evento che accelera un debito | −10 |
| 4.01 | cambio del revisore dei conti | −8 |
| 5.02 | entrata o uscita di un dirigente o consigliere | −4 |
| 1.01 | nuovo accordo rilevante | +4 |

Tutti gli altri codici valgono zero perché sono routine: `2.02` sono i risultati
trimestrali, `9.01` gli allegati, `5.07` l'assemblea degli azionisti. Il totale
positivo è limitato a +12 e quello negativo a −35, così un solo tipo di fatto non
può dominare il punteggio per accumulo.

Due scelte che vale la pena dichiarare:

- **Nessun 8-K non è un dato mancante.** Se in 180 giorni un'azienda non ha
  dichiarato niente, quello *è* il dato: il punteggio resta a 50 e la riga lo
  dice. Per questo la voce ha copertura piena su tutte e 40 le aziende.
- **Non misura il sentiment.** Conta i fatti dichiarati, non il tono con cui
  qualcuno ne ha scritto. Il nome sul sito dice *Fatti rilevanti*, non *News*,
  proprio per non promettere una cosa diversa da quella che fa.

L'unica chiamata di rete è quella che la pipeline faceva già: il JSON dei
depositi di EDGAR, che serviva per verificare il CIK e per le azioni in
circolazione. I dati degli 8-K erano dentro quel file e non li leggevamo.

## Confrontare due aziende

La pagina **Confronta** mette due aziende affiancate: i due totali in alto e,
sotto, gli stessi sei sotto-punteggi riga per riga, con lo scarto.

Serve perché i totali da soli dicono poco. Quasi tutte le aziende stanno fra 45 e
60, quindi sapere che una fa 59 e l'altra 53 non aiuta: la riga utile è quella
dove i due si separano. Fra Intel e NVIDIA, per esempio, il totale differisce di
6 punti, ma la voce *Insider* differisce di 34 — ed è lì che c'è qualcosa da
guardare.

L'indirizzo porta con sé la scelta: `#confronto-NVDA-INTC` apre direttamente quel
confronto, quindi un paragone si può mandare a qualcuno con un link invece di
spiegarlo a parole.

Una cosa dichiarata in fondo alla pagina: se una voce manca a una delle due
aziende, non entra nel confronto e il suo peso viene ridistribuito. I due totali
possono quindi poggiare su basi diverse, e la riga della copertura lo dice.

## Un giro all'ora, e perché può costare quasi niente

Il sito controlla i dati **ogni ora nei giorni feriali**, dalle 06:17 alle 23:17
UTC: 18 giri al giorno invece di 3. La finestra copre la mattina europea e tutta
la giornata di deposito americana — EDGAR accetta i moduli dalle 6 alle 22 di New
York. Le ore fra mezzanotte e le 5 UTC restano fuori: in piena notte americana
non si deposita niente e i prezzi non si muovono, sarebbero richieste alla SEC
senza una ragione.

**I minuti di GitHub Actions sono gratis** sui repository pubblici, quindi
moltiplicare per sei i giri non costa denaro. Il costo vero era un altro, e
andava tolto prima di alzare la frequenza.

Ogni scheda portava dentro `generated_at`, l'ora del giro. Siccome quell'ora
cambia sempre, **tutti e 41 i file risultavano diversi a ogni giro anche quando
non era successo niente**: 4,6 MB di roba nuova che git deve conservare per
sempre, ogni volta. Il workflow aveva già il controllo «nessuna variazione da
salvare», ma non scattava mai, proprio per colpa di quell'orario.

Ora la pipeline confronta ogni scheda con quella già pubblicata **ignorando i
campi-orologio**, e la riscrive solo se è cambiato qualcosa di vero. Un giro a
vuoto tocca il solo `index.json`. A quel punto 18 giri al giorno costano meno di
quanto ne costassero 3 prima.

Da qui nascono due date diverse, ed è giusto che il sito le distingua:

| campo | che cosa dice |
|---|---|
| `generated_at` | quando abbiamo **controllato** l'ultima volta |
| `changed_at` | quando è **cambiato** qualcosa l'ultima volta |

L'intestazione mostra l'ultimo cambiamento, perché è quello che interessa; la
riga sull'età dei dati guarda l'ultimo controllo, perché è quella che si accorge
se il workflow si è fermato. Con un giro all'ora la maggior parte dei giri non
trova niente di nuovo, ed è normale che sia così: gli insider non comprano ogni
ora.

## Se l'aggiornamento si rompe, il sito lo dice

Un sito che si aggiorna da solo ha un modo silenzioso di mentire: il workflow si
ferma e la pagina continua a mostrare numeri vecchi con la stessa faccia di
sempre. Nessuno se ne accorge, perché la data in fondo non la legge nessuno.

Ora il sito guarda l'età dei propri dati e si comporta di conseguenza:

- **sotto le 14 ore**, niente: è il ritmo normale;
- **fra 14 e 60 ore**, una riga grigia sotto l'intestazione che dice quante ore
  hanno i dati, senza allarmare;
- **oltre le 60 ore**, un avviso vero: i giri sono alle 06:17, 12:47 e 22:35 UTC
  dal lunedì al venerdì, quindi il buco più lungo normale è il fine settimana,
  circa 56 ore. Sopra le 60 non è più un fine settimana, è un guasto.

La soglia è scelta così apposta: un avviso che scatta ogni domenica smette di
essere letto dopo due settimane.

## L'elenco: che cosa ha spinto il punteggio

Nell'elenco la colonna *Lettura* diceva la stessa cosa su quasi tutte le righe
(«Leggermente rialzista», «confidenza media»), quindi occupava spazio senza
distinguere niente. Ora la seconda riga dice **quale voce ha spostato di più quel
punteggio dal centro**: non quella con il valore più alto, ma quella il cui
scarto da 50, moltiplicato per il suo peso, pesa di più sul totale.

Su 40 aziende i motori sono otto diversi: `insider` tira giù 16 punteggi e ne
spinge su 1, `mercato` ne spinge 6 e ne tira 8, e così via. È la stessa
informazione che prima si trovava solo aprendo la scheda.

Sopra la tabella c'è un campo di ricerca: filtra per ticker o per nome mentre
scrivi, si combina con il filtro per universo e con l'ordinamento, e quando non
trova niente lo dice invece di mostrare una tabella vuota.

Le intestazioni della tabella sono cliccabili: azienda, punteggio, compra/vende,
flusso netto e prezzo. Il secondo click inverte il verso, i dati mancanti restano
in fondo in entrambi i versi, e a parità di valore l'ordine è alfabetico, così
due giri uguali danno la stessa lista.

## Il colore dice qualcosa

Niente nel sito è colorato per decorazione. Ci sono due sole scale, e ognuna ha
un mestiere.

**La scala divergente** è quella dei punteggi: sette gradini, tre di blu verso la
convinzione rialzista, tre di rosso verso quella ribassista, e un grigio vero al
centro. Le soglie non sono scelte a parte: sono **le stesse del verdetto
scritto**. Un punteggio di 59 è azzurro perché la riga accanto dice «leggermente
rialzista»; se un giorno cambiassimo le soglie del verdetto, il colore
cambierebbe con loro. Colore e parole non possono contraddirsi.

**La scala categoriale** sono le sei tinte delle voci della formula, usate in un
posto solo: la barra della guida che mostra come si dividono i cento punti. Lì il
colore serve a distinguere, non a giudicare, e ogni fetta ha la sua etichetta
accanto.

Tutti e tredici i colori sono stati verificati con uno strumento, non a occhio:
separazione per chi non distingue i rossi dai verdi, contrasto contro la
superficie chiara e contro quella scura, passo di luminosità fra gradini vicini.
Le etichette dentro la barra sono in inchiostro scuro e non in bianco, perché sul
giallo il bianco si ferma a 2,17:1 mentre lo scuro arriva a 8,58:1.

Il grafico del Backtest segue le stesse regole: barre che partono dalla linea
dello zero, blu a destra e rosse a sinistra, e le due barre agli estremi che
raccolgono le code perché un singolo −45% altrimenti schiaccerebbe tutto il
resto.

## Social: tolto, non rimandato

La voce *Social* valeva il 5% ed era anche lei sempre vuota. L'abbiamo tolta dai
pesi invece di lasciarla lì.

Il motivo non è che fosse difficile: è che non sapremmo dire in che direzione
leggerla. Una fonte gratuita e verificabile di attenzione del pubblico esiste —
le visite alle pagine di Wikipedia hanno un'API aperta e sono usate in
letteratura — ma l'attenzione **non ha un segno**: una pagina viene letta di più
sia quando l'azienda annuncia un ottimo trimestre sia quando finisce sotto
inchiesta. Dare un punteggio a un numero di cui non sappiamo il verso vorrebbe
dire inventare un segnale, e questo progetto esiste per non farlo.

I 5 punti sono andati a `insider`, che passa da 30 a 35. È l'unica voce della
formula che abbia un riscontro empirico, per quanto piccolo: nel backtest gli
acquisti dei dirigenti apicali hanno una mediana di +7% a 60 giorni contro +1,4%
degli altri insider.

## Il backtest: che cosa misura e che cosa no

La pagina **Backtest** del sito prende ogni acquisto di un insider degli ultimi
cinque anni (vedi "Cinque anni di storia") e guarda come è andato il titolo nei 60 giorni successivi, al netto del
suo settore. Serve a una cosa sola: controllare se le due regole che la formula
usa per dare più peso ad alcuni acquisti trovano riscontro nei dati.

**Le campagne contano, non le firme e nemmeno gli episodi.** Cinque dirigenti
che comprano lo stesso giorno nella stessa azienda sono un fatto solo, osservato
cinque volte (un *episodio*: una coppia ticker + data). Ma anche episodi diversi
possono essere lo stesso fatto: un insider che compra dieci volte in tre
settimane produce dieci misure a 60 sedute che coprono quasi lo stesso periodo
di borsa. Dal 22 novembre 2026 l'unità è la **campagna**: gli episodi dello
stesso titolo entro 90 giorni dal primo, con il rendimento medio. Ogni riga
riporta campagne, episodi e acquisti, così si vede quanto il conteggio per
acquisto gonfiava le cose; quando le campagne sono meno di cinque il sito lo
dice e toglie il colore al numero.

Quello che il backtest **non** è:

- **Non è il backtest del punteggio.** Guidance e short interest non esistono
  giorno per giorno all'indietro: la guidance la estraiamo dalle call che
  abbiamo scaricato, lo short interest parte dal 2024. Ricostruire il punteggio
  di due anni fa vorrebbe dire inventarlo, e un backtest costruito su un
  punteggio inventato è peggio di nessun backtest.
- **Non è una strategia.** Niente costi, niente slittamento di prezzo, nessuna
  dimensione delle posizioni, nessuna regola di uscita diversa dai 60 giorni
  fissi.
- **Non è un campione grande.** Poche decine di episodi su 40 aziende e due
  anni: abbastanza per farsi un'idea, non per sostenere una tesi.

Il rendimento è sempre al netto del settore di appartenenza (lo stesso
riferimento usato per il track record dei singoli insider), non del mercato:
misura la scelta dell'azienda, non l'andamento generale.

Il calcolo sta in `backtest()` dentro `pipeline/build.py`, gira a ogni
aggiornamento e finisce in `index.json` sotto la chiave `backtest`. Se gli
acquisti misurabili sono meno di dieci, la chiave resta `null` e la pagina
dice che non c'è ancora abbastanza per parlare, invece di mostrare un numero
costruito su tre casi.

## Il 22 novembre 2026: quattro correzioni e i bonus stimati dai dati

Il lavoro doveva essere uno solo: smettere di scrivere a mano i bonus della voce
insider (+18 al cluster, +10 al dirigente apicale, +8 a due compratori) e farli
stimare dal backtest. Preparando i dati per la stima sono venuti fuori tre
errori, e un quarto nei test. Sono stati corretti prima della stima, perché una
stima su dati sbagliati è peggio di un numero scritto a mano.

**1. Form 4 di altri emittenti.** L'elenco dei depositi di un CIK su EDGAR
contiene anche i Form 4 che l'azienda deposita come *azionista* di altre
società. Il parser non controllava l'emittente, così sotto BAC finivano 53
"acquisti" e 98 "vendite" della banca su quote di fondi (uno valeva 103
dollari), sotto KO la vendita di una partecipazione da 2,4 miliardi, sotto INTC
una da 1 miliardo, e lo stesso per JNJ, JPM e GOOGL (tramite GV). Erano contati
come operazioni degli insider. Ora `parse_form4()` confronta `issuerCik` con il
CIK dell'azienda e scarta il documento se non coincide; i documenti scartati
non contano nel limite dei 120 per titolo, e la cache passa a `cache/form4-v2`
perché quelli letti prima vanno riletti tutti, una volta. Lilly Endowment (LLY)
e il trust Walton (WMT) restano: sono azionisti veri dell'azienda.

**2. "Apicale" voleva dire due cose.** La formula dà il bonus a CEO, CFO e
presidente (`rules.is_senior`). Il backtest invece usava il flag "officer" del
Form 4, che vale per qualunque dirigente, compresi il responsabile legale e il
capo contabile: metteva alla prova una regola diversa da quella applicata. Ora
usano la stessa funzione.

**3. Le prove si contano per campagna.** Vedi la sezione sul backtest. Con i
dati puliti: 66 acquisti, 31 episodi, **23 campagne**. Rendimento mediano per
campagna **+1,2%**; contando i singoli acquisti sarebbe +7,0%. I 35 acquisti
dei dirigenti apicali sono 6 campagne, e 25 di quelle righe sono un solo
acquisto di Musk spezzato nel Form 4. Lo stesso raggruppamento vale ora per il
moltiplicatore storico di ogni insider (`rules.person_campaigns`): il
"compratore" di Bank of America aveva 34 acquisti passati contati come 34 prove.

**4. I test nascondevano due fallimenti.** In `tests/test_offline.py` una riga
riassegnava la variabile `bad`, cioè l'elenco dei controlli falliti, e così
cancellava tutti i fallimenti registrati prima: due controlli fallivano da
settimane (previsioni rimaste a pesi vecchi, non errori del codice) e il
riepilogo diceva "0 falliti". Corretti tutti e due, e l'ultimo controllo del
file verifica che l'elenco non venga più sostituito.

### I bonus, stimati

`pipeline/bonus.py` stima i tre bonus con una regressione sulle campagne
(rendimenti limitati a ±50 punti) e restringe ogni stima verso zero quanto più è
incerta: bonus = effetto × τ² / (τ² + incertezza²), con τ = 5 punti percentuali,
a un punto di punteggio per punto percentuale. τ è l'unica scelta fatta a mano;
la pagina Backtest mostra anche che cosa darebbero 3 e 10.

| Regola | Campagne | Effetto misurato | A mano | Stimato |
|---|---|---|---|---|
| Cluster | 1 | −21,6 ± 23,4 | +18 | −1 |
| Due compratori | 1 | +3,3 ± 22,0 | +8 | 0 |
| Dirigente apicale | 6 | −13,3 ± 10,9 | +10 | −2 |

**Come leggerlo.** Un bonus vicino a zero non dice che la regola è sbagliata,
dice che 23 campagne non bastano per saperlo. Lo stimatore è stato provato prima
su dati inventati con effetti veri di 10, 4 e 6 punti: con 5.000 campagne li
ritrova, con 26 li riduce a +2, −1 e +1, e senza effetti veri dà bonus fra −3
e +5 con media zero (i controlli sono in `tests/test_offline.py`). I bonus si
ricalcolano a ogni giro: se le campagne crescono e i dati lo giustificano,
cresceranno da soli. La penalità per la vendita di un dirigente apicale (−10)
resta scritta a mano, perché il backtest guarda solo gli acquisti.

**Che effetto ha oggi.** Quasi nessuno sulla classifica: correlazione di rango
0,999 con quella di prima, verdetti tutti uguali, si muovono solo INTC (−4) e
BAC (−2). In questo momento quasi nessuna azienda ha un cluster o un acquisto di
un dirigente apicale negli ultimi 90 giorni, quindi i bonus sbagliati non
stavano lavorando. Ma alla prossima occasione un cluster avrebbe aggiunto più
di 6 punti al punteggio totale, abbastanza per cambiare verdetto, senza nessun
dato a sostenerlo.

Per applicare i bonus stimati serve vedere tutte le aziende prima di dare il
punteggio a una qualsiasi: `main()` ora costruisce tutte le aziende, stima i
bonus sui loro acquisti passati, ricalcola la voce insider e solo dopo chiude il
settore per gruppo.

## La prova all'indietro delle voci

Il punteggio intero non si può provare nel passato, ma quattro delle sue voci
sì, perché si ricostruiscono giorno per giorno con dati che la pipeline ha già,
senza inventare niente: **prezzo** (dalle chiusure), **settore** (dai Form 4 di
tutto l'universo), **mercato** nella parte che guarda la storia dello short
interest del titolo, e **fatti rilevanti** (dagli 8-K). `pipeline/predict.py`
lo fa a ogni giro.

Per ogni data, una ogni cinque sedute, e per ogni universo: correlazione di
rango fra il valore della voce in quel giorno e il rendimento delle 60 sedute
successive, fra le aziende dello stesso universo. Il confronto è nella stessa
data e nello stesso universo, quindi l'andamento del mercato si annulla e non
serve un riferimento. Le date vicine si sovrappongono, quindi l'incertezza si
misura su blocchi di 60 sedute (87 giorni di calendario). Dal passaggio a
duecento aziende un blocco è un periodo di calendario comune a tutti gli
universi: due settori nello stesso trimestre non sono due prove indipendenti,
perché lo stesso mercato li muove tutti e due, e contarli a parte farebbe
sembrare l'incertezza più piccola del vero. Con cinque anni i blocchi sono una
ventina, quante che siano le aziende. La correlazione che il sito mostra è la
media dei blocchi, la stessa a cui si riferiscono errore e t (fino al 22
novembre era la media di tutte le date, che dà più peso ai blocchi con più date).

Il metodo è stato controllato prima di guardare i risultati: un segnale che
conosce il futuro dà 1,000, uno casuale −0,010. Un test fa lo stesso attraverso
tutta la pipeline, con aziende inventate che crescono ognuna a un ritmo diverso.

| Voce | Correlazione | Incertezza |
|---|---|---|
| Contesto di prezzo | +0,05 | ±0,07 |
| Settore | +0,02 | ±0,06 |
| Mercato (storia dello short interest) | −0,03 | ±0,04 |
| Fatti rilevanti | −0,03 | ±0,06 |

Tutte indistinguibili dal caso. Esplorando a parte, anche le due metà del
prezzo (distanza dal massimo +0,02, momentum −0,03) e il semplice numero di
compratori (+0,03) stanno nel rumore.

Questi sono i valori con due anni di storia (22 novembre 2026); con cinque anni
l'incertezza scende verso ±0,035, e il sito mostra i valori aggiornati.

**Come leggerlo.** Con due anni e quaranta aziende si vedrebbe solo una
correlazione forte, sopra circa 0,12 (il sito calcola la soglia dai dati, e si
abbassa man mano che la storia si allunga); i segnali che funzionano davvero sui
mercati valgono di solito pochi centesimi. Quindi "indistinguibile dal caso" non
dice che una voce è inutile: dice che non c'è nessuna prova per darle più peso.

**Gli 8-K.** Per i fatti rilevanti servono quelli di tutta la storia: la
pipeline li legge dal file dei depositi e dai suoi archivi, e li tiene nella
scheda (`events_hist`). `events_from` dice da quando l'elenco è completo; prima
di quella data la voce non viene ricostruita, perché "nessun 8-K" vorrebbe dire
solo "non lo sappiamo".

## Cinque anni di storia, riempiti a rate

Fino al 22 novembre 2026 la pipeline guardava due anni di Form 4, con un limite
di 120 documenti per titolo. Controllando, quel limite si esauriva molto prima
dei due anni per 12 aziende su 40: i Form 4 di AEHR partivano da aprile 2026,
quelli di META da febbraio 2026, quelli di GOOGL da novembre 2025. Backtest,
campagne e moltiplicatori storici vedevano per loro solo pochi mesi di passato.

Ora la storia è di **cinque anni** per tutto quello che si può scaricare da soli:
prezzi e riferimenti di settore (Yahoo, con stooq tagliato alla stessa
finestra), Form 4 e 8-K. Lo short interest resta quello degli export, circa due
anni. Il punteggio di oggi non cambia: usa gli ultimi 90 giorni di Form 4 e sei
mesi di 8-K, come prima. La storia lunga serve alle prove: backtest e bonus
insider, moltiplicatori storici, prova all'indietro delle voci.

**Gli archivi EDGAR.** Il file principale dei depositi di un'azienda tiene solo
gli ultimi mille moduli. `sources.filings_table(cik, since)` legge anche i file
d'archivio elencati in `filings.files`, solo quelli che servono ad arrivare alla
data voluta, una richiesta ciascuno, e restituisce la data da cui l'elenco è
completo. Così anche BAC, JPM e XOM, che prima avevano gli 8-K solo da pochi
mesi, entrano nella prova dei fatti rilevanti.

**Il riempimento a rate.** Cinque anni di Form 4 di quaranta aziende sono decine
di migliaia di documenti (con duecento aziende il tetto è cambiato: vedi la
sezione successiva). Tutti insieme farebbero durare un giro quasi un'ora, e
il giro dell'ora dopo lo annullerebbe (`cancel-in-progress`), buttando la cache.
Per questo ogni giro scarica al massimo `max_new_filings_per_run` documenti
nuovi (2.000, circa cinque minuti), dal più recente al più vecchio; quelli già
letti restano in cache. Ogni scheda dice se il suo storico è completo
(`history.tx_complete`), l'indice dice per quante aziende lo è, e la pagina
Backtest avvisa finché il riempimento non ha finito: nel frattempo i numeri
del backtest cambiano da un giro all'altro, ed è normale.

**I CIK precedenti.** Controllando il riempimento è venuto fuori che XOM non
aveva **nessuna** operazione di insider, fin dal primo giorno del progetto.
ExxonMobil si è trasferita in Texas con effetto dal 1° luglio 2026 e da allora
deposita con un CIK nuovo (0002115436); tutta la sua storia, Form 4 e 8-K, sta
sotto quello vecchio (0000034088). La voce insider di XOM era quindi sempre
vuota, e il backtest non vedeva Exxon. Ora `config.json` ha `cik_predecessors`
(`{"XOM": ["0000034088"]}`): la pipeline unisce i depositi dei CIK precedenti a
quelli nuovi, prende ogni documento dalla cartella del CIK che l'ha depositato,
e accetta come emittente uno qualunque dei CIK dell'azienda. Se un'altra azienda
dell'universo cambia sede o capogruppo, basta aggiungerla lì.

**Le schede non ingrassano.** La serie lunga dei prezzi (`_px_long`) resta in
memoria durante il giro e non viene scritta. Delle operazioni, la scheda porta
solo quelle che il sito legge (`card_view`, vedi la sezione successiva).

**La prova all'indietro non si accorcia.** Ogni data usa le aziende che in quel
giorno hanno sei mesi di storia alle spalle e 60 sedute davanti, invece delle
sole date comuni a tutte: un titolo quotato da meno tempo entra quando può,
senza tagliare la storia degli altri.

## Duecento aziende

Il 22 novembre 2026 l'universo è passato da 40 a 200 aziende. Il motivo è
quello scritto in fondo, al "Passo successivo": nessuna voce del punteggio ha
ancora una prova di capacità di previsione, e il modo per averla non è cambiare
i pesi ma avere più dati. Duecento aziende vogliono dire più campagne di
acquisto per il backtest e per i bonus insider, e cinque volte più confronti per
data nella prova all'indietro.

**Come sono scelte.** Le 40 aziende di prima restano dove sono, con SMH e SPY:
il registro delle previsioni confronta ogni azienda con il riferimento del suo
universo, e spostarle renderebbe non misurabili le righe già registrate. Le 160
nuove sono otto universi di venti, uno per settore, ognuno con il suo ETF di
riferimento: Software (IGV), Finanza (XLF), Banche regionali (KRE), Sanità (XLV),
Industria (XLI), Energia (XLE), Consumi discrezionali (XLY), Beni di prima
necessità (XLP). In ogni settore le più grandi per capitalizzazione, con tre
esclusioni ragionate:

- **aziende che non esistono più o stanno per sparire**: Coterra (fusa in Devon
  a maggio 2026), Webster Financial (in vendita a Santander), e quelle che sapevo
  già comprate, fuse o in via di esserlo (Ansys, Hess, Norfolk Southern, Kellanova, Kenvue,
  Comerica, Synovus, Kraft Heinz);
- **aziende con uno scorporo (spin-off) negli ultimi cinque anni o in
  programma**: GE, 3M, Danaher, Honeywell, FedEx, Lennar, S&P Global. La
  chiusura che la pipeline prende da Yahoo è corretta per i frazionamenti, ma
  non è detto che lo sia per gli scorpori: in quel caso il giorno dello spin-off
  la serie mostrerebbe un crollo che non è mai successo, e il backtest lo
  conterebbe;
- **i doppioni**: Alphabet, Amazon, JPMorgan e le altre grandi stanno già fra le
  grandi capitalizzazioni USA.

Le banche regionali sono un universo a sé di proposito: i loro insider comprano
spesso, e sono proprio gli acquisti che mancano al backtest.

**BlackRock ha cambiato CIK** il 1° ottobre 2024, quando ha creato una nuova
capogruppo per comprare GIP (il nuovo CIK è 0002012383). È lo stesso caso di
ExxonMobil, quindi `cik_predecessors` ora ha anche `"BLK": ["0001364742"]`.
Quando si aggiunge un'azienda conviene controllare queste tre cose: fusioni in
corso, scorpori recenti, cambi di sede o di capogruppo.

**Che cosa è cambiato nel codice.** Tutto quello che pesa sui tempi del giro:

- *i Form 4 recenti non aspettano*: quelli degli ultimi 120 giorni
  (`sources.RECENT_DAYS`) si scaricano sempre e non contano nel tetto. Un'azienda
  nuova ha la voce insider di oggi dal primo giro; la sua storia arriva a rate,
  e la sua scheda lo dice;
- *il tetto della storia vecchia è doppio*: 4.000 documenti per giro
  (`max_new_filings_per_run`) **e** 35 minuti (`history_minutes_per_run`), il
  primo che finisce. L'orologio tiene il giro sotto l'ora anche se la SEC
  rallenta, perché il giro dell'ora dopo annullerebbe questo;
- *gli archivi dei depositi stanno in cache* (`cache/submissions`): Goldman
  Sachs, Morgan Stanley e Citigroup depositano migliaia di prospetti l'anno, e
  i loro archivi si riscaricavano a ogni giro. Un archivio si riscarica solo se
  la SEC ne dichiara un contenuto diverso, e si tengono solo le cinque colonne
  che la pipeline legge;
- *la mappa ticker → CIK della SEC* (un megabyte) si scarica una volta per giro,
  non una per azienda;
- *se Yahoo rifiuta una richiesta*, vale la copia dei prezzi dell'ultimo giro
  riuscito, purché non abbia più di cinque giorni (`build.prices_or_copy`), e la
  scheda lo dice: la fonte diventa per esempio "yahoo (cache)". Duecento titoli e dieci riferimenti ogni ora sono
  tante richieste, e un'azienda con la chiusura di ieri è meglio di un'azienda
  senza punteggio;
- *le schede portano solo quello che il sito legge*: le operazioni degli ultimi
  120 giorni (il riassunto insider ne guarda 90) oppure, se sono meno, le ultime
  25 (la tabella), più il numero delle operazioni di due anni in `tx_n`. La
  scheda di Meta è passata da 944 KB a 109 KB.

**Che cosa aspettarsi.** Il primo giro dovrebbe durare circa mezz'ora: scarica i Form 4
recenti delle 160 nuove e gli archivi dei depositi. Poi la storia vecchia si
riempie al ritmo di 4.000 documenti a giro: per le grandi aziende americane
cinque anni sono qualche centinaio di Form 4 ciascuna, quindi servono alcuni
giorni feriali. Nel frattempo la pagina Backtest avvisa, e i numeri cambiano da
un giro all'altro. La prova all'indietro (5 secondi con 200 aziende finte da
cinque anni) e la prova dei pesi (meno di un secondo) non rallentano il giro.

**Il controllo dopo il primo giro.** Il primo giro con duecento aziende è
durato circa 38 minuti, e 199 aziende su 200 sono arrivate senza errori. La
duecentesima era BNY Mellon: dal 21 maggio 2026 il suo ticker è `BNY` e non più
`BK`, quindi la SEC e Yahoo non lo trovavano più; in `config.json` ora c'è `BNY`.

Lo stesso controllo ha fatto emergere un errore vecchio. Alcuni Form 4 recenti
dichiarano operazioni di anni prima, depositate in ritardo (Teradyne una del
2014, Visa otto fra il 2018 e il 2020). `rules._ret` prendeva come prima seduta
"la prima seduta dopo la data dell'acquisto", e per una data più vecchia
dell'inizio della serie dei prezzi quella seduta era del novembre 2021: un
acquisto del 2018 veniva misurato sui prezzi del 2021. Gli otto acquisti di Visa
avevano tutti lo stesso rendimento, +21,37%, ed è così che si è visto. Erano 39
acquisti su 237 nel backtest, e il backtest diceva di partire dal 2018 con prezzi
che partono dal 2021. Ora la prima seduta deve cadere entro sette giorni
dall'acquisto (`rules.START_GAP_DAYS`), e le operazioni più vecchie della
finestra di cinque anni restano fuori già in `build_one`: non si possono
misurare, e falserebbero anche la data da cui "parte la storia".

**Che cosa non arriva da solo.** Le trascrizioni delle call dipendono da un
export fatto a mano (vedi la loro sezione): le 160 nuove non le hanno, quindi la
loro copertura dei dati è più bassa e la loro confidenza pure. Il sito lo mostra
azienda per azienda; non è un errore. Lo short interest invece arriva da FINRA
per tutte, dalla stessa sera (vedi "Short interest").

## La regola di maturità: un'osservazione vale solo a orizzonte compiuto

Fino a questa versione `_ret()` in `pipeline/rules.py` accorciava l'orizzonte
quando le sedute disponibili non bastavano: `min(horizon, len(after) - 1)`.
L'intenzione era non buttare via dati. L'effetto era un altro: un acquisto fatto
tre settimane fa veniva misurato su 14 sedute e poi contato insieme a quelli
misurati su 60, come se fossero la stessa cosa.

Quanto pesava, sui dati pubblicati: **16 osservazioni su 117**, alcune misurate
su appena 14 sedute. Tolte quelle, il backtest passa da `n = 117, mediana
+3,16%` a `n = 101, mediana +5,19%`. Il numero migliora, ma non è per questo che
la regola è cambiata: sarebbe cambiata lo stesso se fosse peggiorato. Le 16
osservazioni immature avevano mediana +3,16% — più bassa, perché una parte del
loro rendimento semplicemente non era ancora successa.

Ora `_ret()` restituisce `None` quando l'orizzonte non è compiuto, e
un'osservazione non matura non entra in nessun conto. Il prezzo da pagare è che
gli ultimi tre mesi di acquisti non si vedono ancora nel backtest: è giusto
così, non si vedono perché non sono ancora misurabili.

## Il registro delle previsioni: il punteggio messo alla prova in avanti

Il backtest qui sopra prova due regole dentro la voce insider. Il punteggio
intero non si può provare all'indietro, per il motivo scritto sopra, e questo
resta vero. Ma c'è l'altra strada, quella che non richiede di inventare niente:
**segnare il punteggio di oggi, aspettare, e misurare come è andata.**

Da questa versione in poi ogni giro di aggiornamento aggiunge una riga per
azienda a `site/history.csv`:

```
date,ticker,score,close,bench,bench_close
2026-11-20,INTC,59,54.10,SMH,312.45
```

La riga è datata al **giorno della chiusura**, non al giro: così i giri
successivi della stessa giornata non lasciano doppioni, e il punteggio resta
legato a un giorno di borsa vero. Dopo **90 giorni di calendario** la riga
diventa un risultato: si cerca la prima riga successiva dello stesso ticker
distante almeno 90 giorni, e il rendimento è quello dell'azienda meno quello del
suo riferimento di settore, come ovunque nel progetto. I giorni di calendario,
e non le sedute, reggono anche se il workflow salta qualche giro.

Tre cose rendono il registro una prova e non una vetrina:

- **È in sola aggiunta.** `history.add()` non riscrive mai una riga esistente.
  Una previsione registrata non si corregge dopo aver visto come è andata.
- **È pubblico e scaricabile.** Sta dentro `site/`, quindi chiunque apra il sito
  può prendere il CSV e rifare i conti senza fidarsi dei numeri mostrati.
- **Parte vuoto, e lo dice.** La pagina Backtest mostra il riquadro anche quando
  non c'è ancora niente da mostrare: previsioni registrate, zero misurate, tutte
  in attesa, e la data in cui arriverà il primo esito. I tre numeri tornano
  sempre (registrate = misurate + in attesa), e l'ultima riga di ogni azienda
  è per forza ancora aperta.

Le fasce sono **esattamente** quelle del verdetto scritto sul sito: se un giorno
cambiano lì, vanno cambiate in `BANDS` dentro `pipeline/history.py`, altrimenti
il registro smette di essere confrontabile con quello che il sito dice a parole.
Sotto le dieci previsioni una fascia resta grigia: non è ancora un risultato.

Il primo esito reale arriva circa **90 giorni dopo il primo giro**. Fino ad
allora il riquadro non mostra nessun numero di rendimento, perché qualunque
numero ci mettessimo sarebbe inventato. E quando arriverà, mostrerà anche i
risultati sbagliati: è il solo modo in cui un track record vuol dire qualcosa.

## La prova dei pesi: quanto la classifica dipende dai miei numeri

I sei pesi (35 insider, 20 guidance, 15 mercato, 10 settore, 10 fatti
rilevanti, 10 prezzo) sono scritti a mano. Nessuno li ha stimati e nessuno li
aveva mai messi in discussione: finché restava così, la classifica poteva
benissimo essere soltanto il riflesso di quella scelta arbitraria. Il modulo
`pipeline/stress.py` fa la prova, a ogni giro, e il risultato finisce sul sito
anche nella parte che non fa fare bella figura alla formula.

Tre prove, tutte con la stessa `score.combine()` che usa il sito — non con una
copia riscritta per l'occasione:

1. **Pesi tutti uguali**: la classifica che verrebbe se non avessi nessuna
   opinione.
2. **Una voce tolta alla volta**, guardando solo le aziende che quella voce ce
   l'hanno davvero: altrimenti una voce assente su 35 aziende sembra
   irrilevante quando invece è solo mancante.
3. **600 sorteggi dei pesi**, ognuno libero di valere da metà al doppio di
   quello scritto, poi rinormalizzati a 100.

Si misura la **correlazione di rango** (Spearman) con la classifica
pubblicata, non lo scostamento dei punteggi: il sito si legge come una
classifica, quindi la domanda giusta è se l'ordine tiene.

### Che cosa è venuto fuori

- Con i **pesi tutti uguali** la correlazione di rango è **0,953** e nove delle
  prime dieci restano al loro posto. Su 600 sorteggi non è mai scesa sotto
  **0,877**, e nel sorteggio peggiore sette delle prime dieci tengono.
  **Quindi no: la classifica non è un artefatto dei miei numeri.**
- Ma il motivo è meno lusinghiero. A reggere l'ordine sono **due sole voci**,
  mercato (0,753 senza di lei) e insider (0,756). **Settore, fatti rilevanti e
  contesto di prezzo — trenta punti di peso messi insieme — se tolte non
  cambiano quasi niente** (0,989, 0,991, 0,994): nella forma attuale sono
  quasi decorative.
- La **guidance** è alimentata su **5 aziende su 40**, perché solo per quelle
  ci sono le trascrizioni. Dove c'è sposta parecchio (0,900 su quelle cinque,
  4,6 punti di spostamento medio): è il punto in cui l'estrazione dalle call fa
  il lavoro vero, ed è anche il punto in cui il progetto è più scoperto.

Sulla scheda di ogni azienda la stessa prova lascia una riga grigia sotto la
barra del punteggio: l'intervallo in cui il punteggio finisce in nove sorteggi
su dieci, con i due estremi scritti anche a parole. In media il verdetto resta
lo stesso nell'**88%** dei sorteggi e la fascia è larga **5 punti**, ma non per
tutti: KO sta fra 41 e 52 e tiene il verdetto solo nel 61% dei casi, mentre
AMZN sta fra 51 e 53 e non lo cambia mai.

### Il seme del caso è fisso, e non è un dettaglio

`SEED = 20261120` in `stress.py`. Se i sorteggi cambiassero a ogni giro, ogni
scheda risulterebbe diversa anche quando nei dati non è cambiato niente, e il
sito si riscriverebbe tutto ogni ora per puro rumore — mandando all'aria
l'ottimizzazione dei giri a vuoto descritta sopra. Il test
`due giri sugli stessi dati danno lo stesso identico risultato` esiste per
questo: se qualcuno toglie il seme, il test cade.

La fascia di una singola azienda dipende **solo dai suoi** sotto-punteggi,
quindi una scheda si riscrive quando cambiano i suoi dati e non quando cambiano
quelli di un'altra. L'insieme (correlazioni, prime dieci) dipende da tutte e
sta solo in `index.json`, che viene riscritto comunque a ogni giro.

### Che cosa farne

La prova dice che tre voci su sei non spostano la classifica. Le strade erano
due: rafforzarle perché distinguano di più le aziende, o abbassarne il peso.
Prima di scegliere, il 22 novembre 2026 le voci sono state messe alla prova
all'indietro (sezione seguente): nessuna mostra una capacità di previsione
distinguibile dal caso. Rafforzarle le avrebbe fatte contare di più senza
nessuna prova, cioè la stessa scelta arbitraria dei pesi nella direzione
opposta. Abbassarle, per lo stesso motivo, non ha più fondamento che tenerle.
Quindi i pesi restano dove sono, la prova si rifà a ogni giro, e il sito dice
apertamente che quelle voci oggi sono quasi decorative.

## La prova EDGAR: perché la guidance resta legata alle call

La guidance copre 5 aziende su 40 perché dipende da trascrizioni esportate a
mano. L'idea per allargarla era prendere la guidance dal comunicato stampa che
ogni azienda allega all'8-K item 2.02: pubblico, gratuito, già quasi a portata
di `sources.recent_8k()`. Prima di costruirla, è stata messa alla prova sulle
cinque aziende per cui c'erano già le estrazioni dalle call (22 novembre 2026).

| Azienda | Voci di guidance in call | Ritrovate nel comunicato |
|---|---|---|
| KO | 8 | 6 |
| ORCL | 8 | 5 |
| INTC | 10 | 3 |
| NVDA | 8 | 3 |
| BAC | 7 | 0 |
| **Totale** | **41** | **17** |

Il comunicato porta quasi tutta la guidance numerica di breve periodo
(prossimo trimestre, anno in corso) delle aziende non bancarie, e quasi niente
del resto: capex, anni successivi, commenti. Le banche non mettono guidance nel
comunicato.

Il motivo per cui l'estensione **non è stata fatta** però è un altro: il
sotto-punteggio di guidance ha come base il **tono** medio delle call
(`build.py`, i rialzi e i tagli lo spostano al massimo di 10 punti). Un
comunicato stampa non ha un tono del management confrontabile — lo scrive
l'ufficio stampa, ed è sempre ottimista — e non ha domande e risposte.
Alimentare il sotto-punteggio con quello vorrebbe dire cambiare di nascosto che
cosa misura. EDGAR potrebbe alimentare soltanto la parte "voci alzate o
ridotte", calcolata confrontando i numeri di due comunicati consecutivi: è una
voce diversa, e va decisa come tale.

## Gli universi di titoli

`config.json` contiene dieci liste da venti titoli, e si aggiungono e si
tolgono ticker scrivendo dentro (perché queste, vedi "Duecento aziende"):

| Universo | Universe | Riferimento |
|---|---|---|
| Semiconduttori e apparecchiature | Semiconductors and equipment | `SMH` |
| Grandi capitalizzazioni USA | US large caps | `SPY` |
| Software | Software | `IGV` |
| Finanza | Financials | `XLF` |
| Banche regionali | Regional banks | `KRE` |
| Sanità | Health care | `XLV` |
| Industria | Industrials | `XLI` |
| Energia | Energy | `XLE` |
| Consumi discrezionali | Consumer discretionary | `XLY` |
| Beni di prima necessità | Consumer staples | `XLP` |

La divisione non è estetica. Il sotto-punteggio **Settore** confronta ogni
azienda con le altre dello stesso gruppo: mettere un produttore di chip e una
catena di supermercati nello stesso confronto darebbe un numero senza
significato. Per aggiungere un gruppo basta copiare un blocco di `universi` e
cambiare nome, riferimento e lista; un universo sotto le cinque aziende non
entra nella prova all'indietro. Le prime due liste non vanno toccate: il
registro delle previsioni confronta le loro aziende con SMH e SPY.

Un giro dura fra i dieci e i quaranta minuti, a seconda di quanta storia c'è
ancora da scaricare: i documenti già letti restano in cache, e un documento
depositato non cambia più.

Quello che esce:

```
site/index.json          l'elenco con punteggio e riassunto di ogni azienda
site/data/NVDA.json      il dettaglio, un file per azienda
```

## Come si mette in piedi (una volta sola)

1. **Crea il repository.** Su GitHub: *New repository* → nome `conviction-engine`,
   pubblico. Carica questi file (trascinali nella pagina del repo, oppure
   `git init && git add . && git commit -m "primo commit" && git push`).
2. **Metti il tuo contatto per la SEC.** Settings → Secrets and variables →
   Actions → *New repository secret*: nome `SEC_UA`, valore
   `Nome Cognome tua@email.it`. La SEC rifiuta le richieste anonime.
3. **(Facoltativo) La chiave per l'estrazione.** Stesso posto, nome
   `ANTHROPIC_API_KEY`. Senza, la pipeline salta il passo dell'LLM e va avanti
   con i soli dati pubblici.
4. **Accendi Pages.** Settings → Pages → *Source: GitHub Actions*.
5. **Scegli i titoli.** Apri `config.json` e modifica le liste dentro
   `universi`, se vuoi seguirne altri.

Poi vai in **Actions → Aggiorna i dati → Run workflow**: il primo giro parte a
mano, i successivi da soli ogni sera alle 22:30 UTC, dal lunedì al venerdì.

## Come si usa dal computer

```bash
pip install -r requirements.txt
export SEC_UA="Nome Cognome tua@email.it"
python -m pipeline.build                 # tutto l'universo
python -m pipeline.build --ticker AEHR   # un titolo solo
python -m pipeline.build --offline       # ricalcola dalla cache, senza rete
python -m tests.test_offline             # 24 controlli sulle regole
```

Per vedere il sito in locale: `python -m http.server -d site 8000`.

## Che cosa c'è dentro

```
config.json            ticker, pesi e soglie: le uniche cose da toccare
pipeline/sources.py    rete: stooq e EDGAR, con il parsing separato e testabile
pipeline/rules.py      classificazione delle operazioni, cluster, storico
pipeline/score.py      la formula: sotto-punteggi, pesi, confidenza
pipeline/extract.py    il passo dell'LLM sulle trascrizioni, con cache su disco
pipeline/build.py      mette tutto insieme e scrive site/data.json
site/                  il sito pubblicato: legge data.json a ogni apertura
tests/test_offline.py  i test che girano anche dentro GitHub Actions
```

## Quanto costa

GitHub Actions e Pages sono gratuiti sui repository pubblici. Le fonti dei dati
sono gratuite. L'unica voce a pagamento è la chiave dell'LLM, e solo se usi
l'estrazione dalle trascrizioni: le chiamate vanno in cache su disco, quindi la
stessa call non si paga due volte.

## Limiti da dichiarare, se lo presenti

- I Form 4 arrivano fino a due giorni dopo l'operazione: il segnale non è mai
  dello stesso giorno.
- Il flag del piano 10b5-1 si legge dalle note del documento: nei Form 4 più
  vecchi può mancare, e in quel caso la vendita risulta discrezionale.
- `submissions` di EDGAR tiene le ultime mille registrazioni: gli archivi più
  vecchi si leggono (`sources.filings_table`), ma se uno non risponde l'elenco si
  ferma lì, e la prova all'indietro non usa le date scoperte.
- Il rendimento anomalo usa un ETF di settore come riferimento, non un modello
  di rischio.
- I bonus della voce insider sono stimati su poche decine di campagne: con così
  pochi dati restano vicini a zero qualunque sia l'effetto vero. Con duecento
  aziende le campagne aumentano, man mano che la storia si riempie.
- Le chiusure di Yahoo sono corrette per i frazionamenti, non per i dividendi,
  e non è detto che lo siano per gli scorpori. Per questo le aziende con uno spin-off negli ultimi cinque anni sono
  rimaste fuori; un'azienda aggiunta dopo va controllata.
- La guidance copre solo le aziende per cui c'è un export delle trascrizioni
  fatto a mano: con duecento aziende la maggior parte ha copertura all'80%.
- Lo score non è una raccomandazione di investimento.

## Passo successivo

Il registro delle previsioni darà il primo risultato verso il 18 febbraio 2027.
Fino ad allora nessuna voce del punteggio ha una prova di capacità di previsione:
né i bonus insider né le voci ricostruibili all'indietro. Il modo per uscirne non
è cambiare i pesi, è avere più dati: più aziende, più anni. Dal 22 novembre 2026
le aziende sono duecento; quando la storia di tutte sarà completa, backtest,
bonus e prova all'indietro avranno la base più larga che il progetto può avere
con fonti gratuite.

## Se EDGAR risponde 403

Capita che `www.sec.gov` rifiuti le richieste che arrivano dai server di
GitHub. La pipeline prova tre strade per trovare il codice CIK dell'azienda:
la mappa ufficiale dei ticker, la ricerca di EDGAR, e infine il campo `cik`
di `config.json`. Quel campo è il rimedio: il numero si trova aprendo
`https://www.sec.gov/cgi-bin/browse-edgar?CIK=TICKER&action=getcompany` dal
browser, ed è quello che compare come `CIK#` (per NVIDIA è 1045810, già
scritto nel file).

Se anche i documenti dell'archivio vengono rifiutati, la pipeline si ferma
dopo tre tentativi e lo scrive negli errori invece di insistere cento volte.
In quel caso la strada è far girare `python -m pipeline.build` dal tuo
computer, che ha un indirizzo di casa e non viene bloccato, e caricare il
`site/data.json` che ne esce.
