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
| Trascrizioni delle call | da sole, Alpha Vantage (le richieste che restano delle 25 al giorno); i fatti a mano | dopo ogni stagione degli utili |
| Utili contro attese | da soli, Alpha Vantage (12 aziende al giorno) | dopo ogni trimestrale dell'azienda |
| Calendario delle trimestrali | da solo, Alpha Vantage | una volta al giorno |
| Elenco dei titoli quotati | da solo, Alpha Vantage | una volta alla settimana |

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

Parte già da solo, con tre workflow nella scheda Actions:

- **Aggiorna i dati** (`update.yml`): ogni ora nei giorni feriali, dalle 06:17
  alle 23:17 UTC, e a ogni file caricato in `pipeline/`, `site/` o `config.json`;
- **Scarica trascrizioni** (`trascrizioni.yml`): ogni giorno alle 05:41 UTC, con
  le 25 richieste giornaliere di Alpha Vantage;
- **Tutto il mercato** (`mercato.yml`): ogni tre ore, anche nel fine settimana,
  per il radar, il termometro di tutto il mercato e i 13F.

Tutti e tre si lanciano anche a mano, con "Run workflow". Da sapere: GitHub
sospende le pianificazioni nei repository fermi da 60 giorni, avvisando per
email; basta riattivarle dalla scheda Actions.

### La pagina e i dati nello stesso repository

`site/index.html` lo carichi tu, il resto di `site/` lo scrive il giro orario.
Il giro salva solo i file che genera: fino al 23 novembre salvava tutta la
cartella, e un giro partito prima di un caricamento rimetteva la pagina vecchia
sopra quella nuova (è successo due volte quel giorno).

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

## Dopo un acquisto, seduta per seduta

Dal 23 novembre 2026, con i cinque anni di Form 4 completi, la pagina Backtest ha
una curva in più (codice in `pipeline/eventstudy.py`). È lo "studio di evento"
classico: tutte le campagne di acquisto allineate sul loro primo giorno, e la
media del rendimento oltre il settore, sommato seduta per seduta, dai 20 giorni
di borsa prima ai 120 dopo.

- **L'unità è la campagna**, come nel backtest: gli acquisti dello stesso titolo
  entro 90 giorni dal primo sono una prova sola. Il giorno zero è la prima
  seduta dal primo acquisto.
- **Il rendimento è oltre il settore**: ogni seduta, titolo meno ETF del suo
  universo; la curva è la somma, ed è zero venti sedute prima.
- **Le campagne recenti contano finché hanno prezzi**: per ogni seduta la pagina
  dice su quante campagne è fatta la media, e un punto con meno di 20 campagne
  non si disegna. I valori di una campagna sono limitati a ±50 punti.
- **L'incertezza è a grappoli.** Campagne di aziende diverse negli stessi mesi
  vivono la stessa borsa: l'errore standard raggruppa le campagne in blocchi di
  87 giorni, come la prova all'indietro. La fascia è ±1,96 errori standard.
- **Una tabella separa i tratti che contano**: i 20 giorni prima (gli insider
  comprano dopo un calo?), il giorno zero e il +1 (il Form 4 arriva entro due
  giorni lavorativi, quindi nessuno da fuori poteva comprare lì), e da +2 a +60
  e a +120, cioè quello che chi legge i depositi poteva davvero prendere.
- Si possono confrontare cluster e acquisti isolati, dirigenti apicali e altri,
  e (quando gli utili coprono abbastanza campagne) dopo una trimestrale sopra o
  sotto le attese.

È una figura, non una regola: non cambia il punteggio. Serve a vedere la forma
dell'effetto, e a separare quello che un insider sa da quello che un
investitore da fuori può usare.

## Insider abituali o opportunisti

Sempre dal 23 novembre 2026 (codice in `pipeline/habits.py`). L'idea è di Cohen,
Malloy e Pomorski ("Decoding Inside Information", Journal of Finance, 2012):
molti insider comprano o vendono ogni anno nello stesso periodo, e quelle
operazioni seguono il calendario, non quello che sanno.

- **Abituale**: l'autore ha fatto un'operazione dello stesso verso nello stesso
  mese in ciascuno dei tre anni solari precedenti.
- **Opportunistica**: ha operato in tutti e tre gli anni, ma non in quel mese.
- **Non classificabile**: non ha operato in tutti e tre gli anni.
- Per le vendite la storia conta anche quelle dei piani 10b5-1, ma si
  classificano e si misurano solo le discrezionali, le sole che il punteggio usa.
- Con la storia dall'ottobre 2021 il primo anno intero è il 2022: si
  classificano le operazioni dal 2025. Se la storia di un'azienda non è
  completa, nessuna etichetta.

La pagina Backtest misura il rendimento oltre il settore nelle 60 sedute dopo,
per i tre gruppi, contando ogni insider una volta per mese (persona-mese), con
l'errore a grappoli dello studio di evento. Nelle schede l'etichetta compare
accanto all'operazione, e la voce insider la scrive fra le ragioni. **Per ora
non sposta punti**: con meno di due anni di operazioni classificabili la prova è
piccola, e una regola entra nel punteggio solo quando i dati la sostengono.

## Il termometro degli insider sul mercato

Dal 23 novembre 2026 (codice in `pipeline/thermometer.py`). Il resto del
progetto guarda un'azienda alla volta; il termometro le guarda tutte insieme. È
un'idea classica (Seyhun 1988 e 1992; Lakonishok e Lee, "Are Insider Trades
Informative?", 2001): gli insider nel loro insieme comprano di più quando la
borsa è scesa, e in quegli studi la borsa nei mesi dopo è andata meglio della
media.

- **Finestra di tre mesi**, mobile, letta a ogni fine mese: una trimestrale per
  azienda, così le finestre di acquisto che si aprono dopo i conti pesano
  sempre allo stesso modo.
- **Persone, non dollari**: un insider (azienda, nome) che compra sul mercato
  almeno una volta nella finestra è un compratore; uno che vende fuori da un
  piano 10b5-1 è un venditore. Le assegnazioni e le vendite dei piani non
  contano.
- **La lettura è la quota di compratori**, in percentuale. Le vendite sono
  sempre molte di più, quindi conta se la quota è alta o bassa rispetto alla
  sua storia. Sotto 30 insider attivi la lettura non si dà.
- **Solo la storia intera**: un'azienda entra dal primo mese intero in cui la
  sua storia dei Form 4 non ha buchi. Per saperlo, `sources.insider_transactions`
  dice ora anche il giorno del documento mancante più recente (`holes_until`):
  i documenti si leggono dal più recente al più vecchio, quindi dopo quel
  giorno la storia è completa. Il tetto per azienda
  (`max_filings_per_ticker`) conta come un buco.

La prova, sulla pagina Backtest: per ogni mese chiuso con almeno il 90% delle
aziende coperte, il rendimento dello SPY (il riferimento del gruppo "Grandi
capitalizzazioni USA") dal giorno 5 del mese dopo, quando i Form 4 di fine mese
sono arrivati, a tre e a sei mesi. Tre gruppi uguali di mesi dalla quota più
bassa alla più alta, la pendenza della retta per 10 punti di quota con
l'errore standard di Newey-West (ritardi: orizzonte − 1 + finestra − 1, perché
si sovrappongono sia le finestre sia i rendimenti), e la correlazione di rango
di Spearman. Con dieci anni e rendimenti a sei mesi le prove indipendenti sono
una ventina: la pagina lo dice. **Il termometro non entra nel punteggio**:
descrive il mercato, non sceglie l'azienda. Sulla Home c'è la lettura di oggi;
`#termometro` porta direttamente alla scheda nella pagina Backtest.

## Tutto il mercato: il radar

Dal 23 novembre 2026 (codice in `pipeline/mercato.py`, workflow
`.github/workflows/mercato.yml`). Le duecento aziende sono una scelta; il radar
guarda tutte le società americane. Scaricare i Form 4 uno alla volta, come per
le duecento, non reggerebbe (più di mille al giorno), ma la SEC pubblica due cose
che lo rendono possibile:

- **i data set trimestrali dei Form 3, 4 e 5** ("Insider Transactions Data
  Sets"), dal 2006: un file zip di circa 10 MB per trimestre di deposito, con
  tutte le operazioni già in tabella. L'elenco dei file si legge dalla pagina
  della SEC, perché gli indirizzi cambiano cartella;
- **l'indice giornaliero di EDGAR** (`master.AAAAMMGG.idx`), pubblicato la sera:
  per i giorni dopo l'ultimo data set si leggono i Form 4 del giorno, uno alla
  volta, al ritmo di sempre (una richiesta ogni 0,15 secondi). I giorni si
  leggono dal più recente, e un giorno lasciato a metà riprende al giro dopo.

Il workflow **Tutto il mercato** gira ogni tre ore e salva tutto nella cache
`mercato-` (non nel repository); il giro orario la ripristina e scrive
`site/radar.json`. **Il radar è quindi di ieri sera**, non dell'ultima ora.

- Entra un'azienda se nei 90 giorni amministratori e dirigenti hanno comprato
  sul mercato (codice P), a un prezzo di almeno 1 dollaro, per almeno 50.000
  dollari in tutto. Chi possiede più del 10% senza essere amministratore o
  dirigente (spesso un fondo) si mostra a parte e non basta a farla entrare.
- In cima i cluster (tre persone diverse entro 30 giorni), poi le aziende con
  più compratori, poi quelle con più dollari. Filtri: solo cluster, solo con un
  dirigente apicale, solo le aziende seguite.
- Le aziende fuori dalle duecento non hanno punteggio: non hanno prezzi,
  short interest e call nel sistema. Il radar dice dove guardare.
- Servono un simbolo di borsa: senza, sono fondi non quotati o società private,
  dove gli amministratori che "comprano" sottoscrivono quote.
- Dal 2026 anche amministratori e dirigenti delle società estere quotate negli
  Stati Uniti depositano il Form 4 (TSMC, Bradesco, Amdocs…): entrano nel radar.

Il primo giro scarica un'ottantina di data set e comincia a leggere i giorni del
trimestre in corso (circa mille Form 4 al giorno): servono alcuni giri per
arrivare in pari, e la pagina dice fino a che giorno i depositi sono stati letti.
Per farlo partire subito: scheda Actions, "Tutto il mercato", "Run workflow".

Dagli stessi data set viene il **termometro di tutto il mercato**: per ogni
mese dal 2006, quanti amministratori e dirigenti hanno comprato e quanti hanno
venduto (una volta per persona e per mese). Le vendite dei piani 10b5-1 qui
restano dentro, perché i data set non le distinguono per tutti gli anni: la
quota è più bassa di quella delle duecento aziende ma è la stessa misura per
vent'anni. La prova usa trent'anni di chiusure dello SPY, scaricate dallo
stesso workflow. Nella pagina Backtest due pulsanti passano da un termometro
all'altro; la Home mostra quello di tutto il mercato.

## I grandi fondi (13F)

Codice in `pipeline/fondi.py`, scaricato dallo stesso workflow. Ogni gestore con
più di 100 milioni di dollari in azioni americane deposita ogni trimestre il
Form 13F con le sue posizioni, entro 45 giorni; la SEC li pubblica in tabella
(circa 90 MB ogni tre mesi di depositi, sei file per giro, dieci anni in tutto).

- Per ogni azienda e trimestre: quanti gestori la possiedono (solo azioni, non
  opzioni né obbligazioni), quante azioni in tutto, e l'**ampiezza**: detentori
  diviso il numero di gestori che hanno depositato. La variazione dell'ampiezza
  è la misura di Chen, Hong e Stein (2002).
- Si usa il 13F-HR originale (non le correzioni), il più recente se un gestore
  ne ha due per lo stesso trimestre. Di ogni file si tiene solo il trimestre
  principale: il primo giro, il 23 novembre, aveva preso anche i depositi in
  ritardo (e qualcuno datato 2001), con trimestri di poche decine di gestori.
- **Il CUSIP.** Il 13F identifica il titolo con il CUSIP, che la pipeline non ha:
  lo si trova dal nome dell'emittente, ripulito (maiuscole, niente "INC",
  "/DE/", abbreviazioni dei gestori sciolte: "INSTRS", "PETE", "COS"…), e fra i
  CUSIP che corrispondono vince quello con più detentori. Poi si contano tutte
  le righe con quel CUSIP (le prime otto cifre), comunque il gestore abbia
  scritto il nome: al primo giro General Motors risultava in mano a 223 gestori,
  perché molti scrivono "GENERAL MTRS CO". Le aziende che hanno
  cambiato nome in dieci anni (Facebook, Anthem, Schlumberger…) hanno i nomi di
  allora in `fondi.ALIASES`. L'indice del sito, alla voce `funds.map`, dice per
  ogni azienda quale CUSIP è stato scelto e con quale nome, e `funds.missing`
  quali non sono state trovate; in `config.json`, `cusip` fissa a mano quello
  giusto e `nomi_13f` aggiunge altri nomi.
- Nella scheda, scheda **Fondi**: gli ultimi dodici trimestri. Nella prova
  all'indietro (pipeline/predict.py) la variazione dell'ampiezza è una voce in
  più, usata solo dal 46° giorno dopo la fine del trimestre, quando è pubblica.
  **Non entra nel punteggio** finché la prova non dice che c'è un segnale.

## Chi supera il 5% per contare (13D)

Codice in `pipeline/attivisti.py`, nel giro orario. Chi supera il 5% di una
società senza essere un investitore passivo deposita lo Schedule 13D: attivisti
(Icahn, Elliott, Trian…), ma anche fondatori e azionisti di controllo.

- Il JSON dei depositi di ogni azienda (lo stesso dei Form 4 e degli 8-K) elenca
  i 13D in cui l'azienda compare: sia quelli depositati **su di lei**, sia quelli
  che lei ha depositato **su altre società** (le banche ne depositano molti per
  i loro fondi: il primo giro, il 23 novembre, ne contava 158 per sbaglio).
- Per questo si legge l'intestazione del deposito (`.hdr.sgml`): dice chi l'ha
  depositato e su quale società. Conta solo se la società è la nostra; finché
  l'intestazione non è letta, non conta. La quota si legge dalla copertina del
  documento, solo per i 13D iniziali nostri. Una volta sola, poi resta in cache
  (`cache/13d/`), al più 300 intestazioni nuove per giro.
- Dal 18 dicembre 2024 il modulo si chiama "SCHEDULE 13D" ed è in XML; prima
  "SC 13D". Si leggono tutti e due. Le correzioni (13D/A) si contano soltanto.
- Nella scheda: i 13D dell'ultimo anno, e il più recente fra i dati in alto.
  Nella pagina Backtest lo studio di evento (Brav, Jiang, Partnoy e Thomas,
  2008) sulle duecento aziende: le grandi società ne ricevono pochi, e la
  pagina lo dice quando sono meno di 20. **Non sposta punti.**

## Che cosa dicono i dirigenti: i comunicati

Dal 24 novembre 2026, codice in `pipeline/comunicati.py`, workflow
`comunicati.yml` ogni tre ore (cache `comunicati-`, che il giro orario legge e
basta). Il terzo segnale, accanto a quello che gli insider **fanno** e a quello
che il management dice nelle **call**.

- **Da dove.** Le frasi fra virgolette dei dirigenti nei comunicati stampa che
  l'azienda allega agli 8-K (allegato EX-99, di solito EX-99.1), negli 8-K con
  item 2.02, 7.01 o 8.01. È la stessa frase che esce sui giornali, presa alla
  fonte: gratis, legale, dieci anni di storia. I siti di notizie no: i termini
  vietano di raschiarli e dai server di GitHub bloccano le richieste.
- **Quali frasi.** Il paragrafo con la citazione deve nominare un ruolo (chief
  executive, CEO, president, chair, chief financial, CFO, chief operating,
  founder) e un verbo di attribuzione (said, stated, added…); conta anche il
  paragrafo che continua la citazione ("continued Doe"). Le citazioni di
  analisti e le parole fra virgolette delle avvertenze legali restano fuori.
- **Il tono, senza modelli.** Il dizionario di Loughran e McDonald
  (`input/lm/`, vedi il LEGGIMI): positive meno negative ogni 100 parole, con la
  loro regola sulla negazione. Si misura anche tutto il comunicato.
- **Il segnale è lo scarto dal solito**: tono meno la media degli otto
  comunicati prima dell'azienda (almeno quattro), diviso per la loro deviazione
  standard. Huang, Teoh e Zhang (2014) trovano che un tono anormalmente
  positivo precede rendimenti più bassi.
- **Due richieste per comunicato** (l'indice del deposito, poi l'allegato), una
  volta sola, dal più recente al più vecchio. Qualche decina di migliaia di
  comunicati per dieci anni: ci vogliono alcuni giri per arrivare in pari.
- Nella scheda, alla voce **Comunicati**: le ultime frasi con il loro tono, e
  accanto quello che gli insider hanno fatto negli ultimi 90 giorni ("parole e
  fatti non vanno d'accordo" se il tono è più ottimista del solito e gli
  insider vendono, o il contrario). Nella pagina Backtest, due voci candidate
  nella prova all'indietro. **Non sposta punti.**

## Gli avvisi: il feed RSS degli acquisti

Dal 23 novembre 2026 (codice in `pipeline/feed.py`). A ogni giro la pipeline
scrive `site/feed.xml` (italiano) e `site/feed-en.xml` (inglese), pubblicati con
il sito: `https://mpascale19.github.io/conviction-engine/feed.xml`. L'indirizzo
è sulla Home, con un pulsante per copiarlo, e la pagina lo dichiara ai browser
e ai lettori di feed (`<link rel="alternate">`).

- **Che cosa esce**: ogni acquisto sul mercato degli ultimi 60 giorni, una voce
  per insider e giorno (le righe dello stesso Form 4 si sommano), e ogni nuovo
  cluster. Le vendite no.
- **Che cosa dice**: chi, che ruolo, quante azioni, a che prezzo medio, per
  quanto in tutto, se fa parte di un cluster, e il punteggio dell'azienda in
  quel momento. Il link porta alla scheda nella lingua del feed
  (`?lang=it` o `?lang=en`, che il sito ora legge).
- **Una voce uscita non cambia più.** Il feed nuovo rilegge quello del giro
  prima (è nel repository, perché `update.yml` fa il commit di `site/`) e
  tiene com'erano le voci già uscite; quelle nuove hanno l'ora del giro che le
  ha trovate. Un giro che non trova niente lascia il file identico. Se
  un'azienda manca per un giro (un errore, o un giro su un solo titolo), le
  sue voci restano finché sono dentro i 60 giorni.
- **Il primo giro** non ha un feed precedente: le voci prendono la data
  dell'operazione e non dicono il punteggio, perché quello di oggi non è
  quello di quel giorno.
- L'indirizzo del sito sta in `config.json`, `site_url`.

## Utili contro attese, calendario e titoli quotati (Alpha Vantage)

Dal 23 novembre 2026 la stessa chiave di Alpha Vantage serve anche a tre cose
che costano poco (codice in `pipeline/alphavantage.py`):

| Richiesta | Che cosa dà | Quante richieste |
|---|---|---|
| `LISTING_STATUS` | i titoli quotati oggi | 1 alla settimana |
| `EARNINGS_CALENDAR` | le prossime trimestrali di tutte le aziende, per tre mesi | 1 al giorno |
| `EARNINGS` | per ogni trimestre: utile per azione annunciato, attese degli analisti, sorpresa | 1 per azienda: la prima volta per tutte, poi dopo ogni sua trimestrale |

L'elenco dei titoli *tolti* dalla borsa (`state=delisted`) non si chiede: il
23 novembre 2026 Alpha Vantage rispondeva `{}`, anche al suo esempio ufficiale.
Quella risposta vuota ha fatto cadere il primo giro, con le trascrizioni del
giorno. Da allora ogni pezzo di questa parte, se va storto, finisce nel
riepilogo come riga `ERR` e il giro continua con gli altri e con le
trascrizioni; un elenco dei titoli con meno di metà dell'universo non si usa,
perché farebbe comparire cento avvisi falsi.

Oltre alle 25 al giorno c'è un secondo limite: al massimo una richiesta al
secondo. Il secondo giro del 23 novembre l'ha superato (le richieste di questa
parte partivano una dietro l'altra) e il messaggio, che cita anche le 25 al
giorno, è stato preso per il limite del giorno; nella stessa raffica il
calendario è arrivato vuoto ed è stato salvato. Ora fra una richiesta e l'altra
passano almeno 1,5 secondi, anche fra gli utili e le trascrizioni; se Alpha
Vantage chiede comunque di rallentare, il giro aspetta dieci secondi e riprova
una volta; un calendario vuoto o senza nessuna azienda dell'universo non si
salva e si richiede al giro dopo.

**Chi usa le 25 richieste.** Sono una quota sola, e passano tutte dal workflow
**Scarica trascrizioni**, una volta al giorno. Prima l'elenco dei titoli, se ha
più di una settimana; poi il calendario; poi gli utili di al massimo 12 aziende
(prima quelle che hanno appena annunciato i risultati, poi quelle che non li
hanno mai avuti, nello stesso ordine delle trascrizioni); con quello che resta,
le trascrizioni. Il numero 12 si cambia dal pulsante *Run workflow* (campo
`earnings`). Con 200 aziende il primo giro completo degli utili dura circa 17
giorni; nel frattempo arrivano 10-12 trascrizioni al giorno invece di 20-25.
Dopo, gli utili costano in media 2-3 richieste al giorno, di più nelle settimane
delle trimestrali.

Un'azienda si rilegge quando una sua trimestrale è passata (dal calendario o da
un 8-K con item 2.02) ed è più recente dell'ultima che abbiamo. Se Alpha
Vantage non ha ancora il dato, si riprova per tre giorni, poi si aspetta la
trimestrale dopo.

**Dove restano i dati.** Nella cache di GitHub Actions, cartella
`cache-alphavantage`, non nel repository: sono dati di Alpha Vantage. Il
workflow delle trascrizioni la salva; il giro orario del sito (`update.yml`) la
legge con `actions/cache/restore`, che legge e non salva mai, così una copia
vecchia non può prendere il posto di quella nuova. Nelle schede finiscono solo i
numeri che il sito mostra: gli ultimi dodici trimestri e la prossima trimestrale.
Se la cache sparisce (GitHub la cancella dopo sette giorni senza uso, ma il giro
orario la usa ogni ora) gli utili si riscaricano da capo, e nel frattempo il
sito funziona come prima.

**Che cosa ne fa il sito.**

- Nella scheda di ogni azienda: la scheda *Utili* con utile, attese e sorpresa
  degli ultimi trimestri, e la data della prossima trimestrale.
- Un ticker che non compare più fra i titoli quotati diventa un avviso nei
  problemi della scheda, con l'invito a controllare e a sostituirlo in
  `config.json`. È il caso BK→BNY, che sarebbe stato trovato da solo.
- **Un bonus nuovo nella voce insider, stimato dai dati.** La domanda è: un
  insider che compra dopo una trimestrale sopra le attese conta di più? Ogni
  campagna di acquisti guarda l'ultima trimestrale annunciata *prima* del primo
  acquisto (entro 100 giorni; il giorno stesso dell'annuncio non conta, perché
  l'acquisto può essere venuto prima dei numeri). Il bonus `beat` si stima come
  gli altri tre in `pipeline/bonus.py`, con lo stesso restringimento verso zero,
  ma sulle sole campagne per cui il dato c'è e con le altre tre caratteristiche
  come controllo: così cluster, due compratori e dirigente apicale non cambiano
  per il fatto che gli utili ci siano o no. A mano valeva zero, quindi parte da
  zero. Se la trimestrale era in linea o sotto, la voce insider lo scrive fra le
  ragioni ma non toglie punti: non c'è una regola per quel caso.
- **Una voce in più nella prova all'indietro**, `surprise`: per ogni data, la
  sorpresa dell'ultima trimestrale (entro 100 giorni) contro il rendimento delle
  60 sedute dopo, fra aziende dello stesso universo. È la prova classica della
  deriva dopo l'annuncio degli utili. Nel punteggio non entra: se la prova
  mostrasse un segnale, sarebbe la ragione per farla entrare.

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

## Dieci anni di storia, e gli scorpori

Dal 23 novembre 2026 la storia è di **dieci anni** (`months_of_history`: 120 in
`config.json`, `HIST_YEARS = 10` in `pipeline/sources.py`): Form 4, 8-K e prezzi
dall'autunno 2016. Lo short interest di FINRA resta di cinque anni. Perché:

- con cinque anni le campagne di acquisto erano circa 500, e le prove (bonus,
  curva dopo un acquisto) avevano bande larghe; con dieci sono circa il doppio;
- abituali e opportunisti si classificano dal 2020 invece che dal 2025;
- il periodo comprende un ciclo intero: il 2018, il crollo del 2020, i tassi del
  2022.

La storia nuova si riempie a rate come la prima volta (i recenti sempre subito),
e il sito torna a dire, in giallo, quante aziende sono complete. Per farla in
giornata il tetto per giro è salito da 4.000 documenti e 35 minuti a 10.000
documenti e 50 minuti: un giro dura quasi un'ora, e quello dell'ora dopo aspetta
in coda invece di fermarlo (vedi `update.yml`). Il ritmo verso la SEC non cambia,
al massimo una richiesta ogni 0,15 secondi, sotto il limite di 10 al secondo:
cambia solo quanto a lungo si scarica in ogni giro.

**Gli scorpori.** La chiusura di Yahoo è corretta per i frazionamenti, non per
gli scorpori né per i dividendi straordinari: il giorno in cui gli azionisti di
Pfizer ricevono le azioni di Viatris, la serie di Pfizer mostra un crollo che
nessuno ha subìto. Con cinque anni le aziende con uno scorporo recente erano
state lasciate fuori dall'universo; con dieci nell'universo ce ne sono altre,
con scorpori fra il 2017 e il 2021. Le loro date stanno in `config.json`, alla voce
`corporate_actions`:

| Titolo | Che cosa | Data |
|---|---|---|
| HLT | scorporo di Park Hotels e Hilton Grand Vacations | gennaio 2017 |
| BKR | dividendo straordinario della fusione Baker Hughes-GE | luglio 2017 |
| MET | scorporo di Brighthouse Financial | agosto 2017 |
| EQT | scorporo di Equitrans Midstream | novembre 2018 |
| RTX | scorporo di Carrier e Otis, fusione con Raytheon | aprile 2020 |
| PFE | Upjohn in Viatris | novembre 2020 |
| MRK | scorporo di Organon | giugno 2021 |

Per ognuna `rules.neutralize` cerca, entro una settimana dalla data, la seduta
peggiore rispetto al settore; se è davvero un salto (più di 5 punti sotto),
corregge i prezzi precedenti in modo che quel giorno il titolo faccia quanto il
settore. Si perde un giorno di informazione vera, ma non si inventa un crollo.

Le date le ho scritte io, e un elenco a mano può avere buchi. Per questo ogni giro
elenca nell'indice (`price_jumps.flagged`) ogni seduta in cui un titolo ha fatto
peggio del suo settore di più di 25 punti. Quelle non si correggono, perché un
crollo vero è informazione, ma si guardano a mano: se è uno scorporo, la data va
aggiunta a `config.json`.

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

## Quattrocento aziende: le medie e le piccole

Il 24 novembre 2026, con la storia a dieci anni completa per le prime 200,
l'universo è passato a 400: **100 medie capitalizzazioni** dall'S&P MidCap 400 e
**100 piccole** dall'S&P SmallCap 600. Negli studi (Lakonishok e Lee, 2001, fra
gli altri) gli acquisti degli insider dicono di più nelle società piccole, che
gli analisti seguono meno: con queste si può vedere se vale anche qui. Il
backtest e la curva "dopo un acquisto" hanno ora la divisione per dimensione.

**Come sono scelte: una regola fissa, che chiunque può rifare** (i file sono in
`input/indici/`, lo script è `input/indici/scegli.py`):

- gli elenchi degli indici sono quelli di Wikipedia al 24 novembre 2026
  ("List of S&P 400 companies", "List of S&P 600 companies"), con il settore GICS;
- si tengono i settori delle prime 200: tecnologia, finanza, sanità,
  industria, energia, consumi discrezionali, beni di prima necessità (fuori
  immobiliare, utility, materiali e comunicazioni);
- si tolgono le aziende già seguite, i ticker con un punto e, fra due classi
  della stessa azienda, la seconda;
- i 100 posti di ogni indice si dividono fra i settori in proporzione;
- dentro un settore decide l'impronta SHA-1 di "conviction-engine|TICKER": un
  sorteggio, non una scelta. Scegliere le aziende guardando gli acquisti degli
  insider avrebbe gonfiato il backtest.

**Il riferimento** di ogni azienda è l'ETF del suo indice: IJH per le medie, IJR
per le piccole. Il rendimento "oltre il settore" per loro è oltre il loro
indice. La storia dei Form 4 delle nuove si riempie a rate, come per le prime:
circa un giorno di aggiornamenti.

**Insieme, il bonus "dopo utili sopra le attese" si è fermato.** Su dieci anni
la stima diceva −3 punti, ma il dato c'era solo nel 16% delle campagne (Alpha
Vantage dà gli utili di una dozzina di aziende al giorno). Con così poche
campagne la stima può dipendere da quali aziende hanno il dato: ora una voce
così si mostra ma non dà punti finché il dato non c'è in almeno metà delle
campagne (`bonus.MIN_KNOWN`).

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

`config.json` contiene dieci liste da venti titoli e due da cento, e si
aggiungono e si tolgono ticker scrivendo dentro (perché queste, vedi "Duecento
aziende" e "Quattrocento aziende"). Le due da cento hanno `"dimensione"`:
"mid" o "small".

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
| Medie capitalizzazioni USA | US mid caps | `IJH` |
| Piccole capitalizzazioni USA | US small caps | `IJR` |

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
pipeline/trascrizioni.py  il giro giornaliero di Alpha Vantage: trascrizioni in Word
pipeline/alphavantage.py  utili contro attese, calendario e titoli quotati
pipeline/eventstudy.py    la curva "dopo un acquisto" della pagina Backtest
pipeline/habits.py        insider abituali o opportunisti (Cohen, Malloy e Pomorski)
pipeline/thermometer.py   il termometro degli insider sul mercato, e la sua prova
pipeline/feed.py          gli avvisi: il feed RSS degli acquisti (site/feed.xml)
pipeline/mercato.py       tutto il mercato: data set dei Form 4, indice giornaliero, radar
pipeline/fondi.py         i grandi fondi: data set 13F, ampiezza della proprietà
pipeline/attivisti.py     gli Schedule 13D delle duecento aziende
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
- Le chiusure di Yahoo sono corrette per i frazionamenti, non per i dividendi
  né per gli scorpori. Gli scorpori noti degli ultimi dieci anni si correggono
  (vedi "Dieci anni di storia, e gli scorpori"); i salti sospetti si elencano
  nell'indice per un controllo a mano. Un'azienda aggiunta dopo va controllata.
- La guidance copre solo le aziende per cui c'è un export delle trascrizioni
  fatto a mano: con duecento aziende la maggior parte ha copertura all'80%.
- Gli utili contro attese sono quelli di Alpha Vantage: l'utile «rettificato» e
  la media delle stime che Alpha Vantage raccoglie, non quella di un fornitore a
  pagamento. Una sorpresa calcolata su attese vicine a zero può venire enorme;
  il bonus guarda solo il segno, la prova all'indietro solo l'ordine.
- Il termometro degli insider conta le persone di duecento grandi aziende
  americane, non di tutto il mercato, e la sua prova ha una ventina di
  osservazioni indipendenti: è una descrizione, non un segnale di mercato.
- Il radar di tutto il mercato è di ieri sera (l'indice giornaliero di EDGAR
  esce dopo le 22 di New York) e non ha punteggi. I suoi acquisti sono quelli
  dichiarati nei Form 4 originali: una correzione (4/A) non si legge.
- I 13F arrivano fino a 45 giorni dopo la fine del trimestre e il titolo si
  riconosce dal nome: una corrispondenza sbagliata è possibile, e l'indice
  elenca quelle scelte per un controllo a mano.
- Il feed RSS arriva con il giro che trova il Form 4: fino a un'ora dopo il
  deposito, e il Form 4 arriva fino a due giorni lavorativi dopo l'operazione.
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
