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
| Prezzi e volumi | stooq.com | sì, senza chiave |
| Operazioni degli insider (Form 4) | SEC EDGAR | sì, senza chiave |
| Rendimento del settore | stooq.com (ETF di settore) | sì |
| Tono e guidance dalle call | il tuo export + LLM | sì, se metti i testi in `input/transcripts/` |
| Revisioni delle stime, short interest | Bloomberg | **no**: export manuale |
| Concorrenti | SEC EDGAR | sì, quando aggiungi i loro ticker |
| Social | — | no |

Quello che manca non blocca niente: il peso del sotto-punteggio assente viene
ridistribuito sugli altri, lo score esce lo stesso e `data.json` dice che cosa
mancava e con quale confidenza. Oggi la parte pubblica copre circa il 40% dei
pesi; con le trascrizioni si arriva al 60%.

## Manutenzione

### Il gesto normale

**Actions -> Aggiorna i dati -> Run workflow.** Due o tre minuti e il sito e'
aggiornato. Parte da solo anche quando carichi file in `pipeline/`, `input/`,
`config.json` o `site/`: in quel caso non devi premere niente.

### Cosa invecchia, e ogni quanto

| Dato | Come si aggiorna | Ogni quanto serve |
|---|---|---|
| Prezzi di chiusura | da solo, Yahoo | a ogni giro |
| Operazioni Form 4 | da solo, SEC EDGAR | a ogni giro |
| Azioni in circolazione | da solo, SEC | a ogni giro |
| Short interest | export manuale | due volte al mese |
| Trascrizioni delle call | export manuale | una volta a trimestre |

Lo short interest lo pubblicano FINRA e le borse due volte al mese, a meta' e a
fine mese, e compare sui terminali qualche giorno dopo. Per aggiornarlo si
riesporta e si sostituisce il CSV intero: il file e' piccolo e la pipeline usa
sempre l'ultima riga.

Le trascrizioni servono solo dopo una earnings call, e solo per le aziende che
segui davvero. Il tono usa le ultime quattro call: se ne salti una, il
sotto-punteggio continua a funzionare con quelle che ci sono.

### Aggiungere un'azienda

Si mette il ticker nella lista giusta dentro `config.json` e si lancia il
workflow. Se nel log compare un errore di verifica del CIK, vuol dire che la
mappa della SEC sbaglia su quel ticker: si aggiunge il numero a mano nel campo
`cik` dello stesso file.

### Farlo partire da solo

In `.github/workflows/update.yml` ci sono due righe commentate sotto
`schedule`. Togliendo il cancelletto l'aggiornamento parte ogni giorno feriale
alle 22:30 UTC, dopo la chiusura di New York. Da sapere: GitHub sospende le
pianificazioni nei repository fermi da 60 giorni, avvisando per email; basta
riattivarle dalla scheda Actions.

### Se qualcosa si rompe

| Sintomo | Causa quasi certa |
|---|---|
| 403 dalla SEC | il secret `SEC_UA` non e' "Nome Cognome email@dominio" |
| un ticker salta con errore sul CIK | la mappa SEC sbaglia: metti il CIK in `config.json` |
| un'azienda resta senza prezzi | Yahoo e stooq hanno entrambi rifiutato; l'errore e' scritto in `errors[]` e nessun punteggio viene inventato |
| il workflow si ferma sui test | un errore nel codice: i test girano **prima** di scaricare qualsiasi cosa, quindi i dati pubblicati non si rovinano mai |
| il sito mostra dati vecchi | il secondo riquadro del workflow (`pubblica`) non e' ancora verde |
| un giro fallisce sul salvataggio | due giri ravvicinati: il piu' recente ha gia' scritto. Non si perde niente, i dati li rigenera il giro dopo |

### Perche' i dati non si fondono mai

I file in `site/` sono generati per intero a ogni giro: la versione appena
calcolata e' sempre quella buona. Per questo il workflow non fa `git pull
--rebase` ma riparte da `origin/main` e riscrive sopra. Un rebase proverebbe a
fondere due versioni degli stessi 41 file, che differiscono su ogni riga per
l'orario, e finirebbe in conflitto su tutti.

Allo stesso modo, se parte un giro mentre un altro e' in corso, il vecchio viene
fermato (`cancel-in-progress`): il nuovo rifa' tutto comunque.

### Il controllo dopo ogni giro

Tre cose, trenta secondi: la data in alto nel sito deve essere quella di adesso,
il riquadro rosso degli errori non deve comparire, e nel log del workflow ogni
riga `CIK` deve avere accanto il nome giusto dell'azienda.

## Le trascrizioni delle call

Il testo delle earnings call arriva dall'export di Bloomberg ed e' materiale
sotto licenza: **non va nel repository pubblico**. Per questo la pipeline legge
due cartelle diverse.

```
input/transcripts/INTC_2026Q3.txt    il testo: resta sul tuo computer (.gitignore)
input/extracted/INTC_2026Q3.json     i fatti estratti: questi si', nel repo
```

Il nome del file decide a quale azienda appartiene la call: il ticker sta
all'inizio, prima del primo trattino o underscore. `INTC_2026Q3.txt` finisce su
Intel, `transcript (3).txt` su nessuno. Se un export contiene piu' call
accodate, va diviso in un file per trimestre.

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

Il consenso degli analisti e' un prodotto a pagamento: la SEC non lo pubblica,
perche' non e' un dato dell'azienda. Ma la tesi del progetto -- *gli insider
comprano mentre le aspettative peggiorano* -- non ha bisogno degli analisti:
l'azienda stessa dice a ogni call se alza o abbassa la propria guidance, e
quella frase e' pubblica.

Ogni voce di guidance estratta porta una `direction`: `alzata`, `ridotta`,
`confermata`, `prima` (data per la prima volta). Il sotto-punteggio
**Guidance e tono** parte dal tono medio delle ultime quattro call e lo corregge
di 2,5 punti per ogni voce alzata o ridotta nell'ultima call, con un massimo di
dieci punti in piu' o in meno.

Quando l'azienda taglia la guidance *e* un insider ha comprato negli ultimi 90
giorni, il sito lo scrive: "gli insider hanno comprato mentre l'azienda tagliava
la guidance". E' il segnale che il progetto esiste per trovare, e costa zero.

## Short interest

Export manuale da Bloomberg (`SI <GO>`), un file per azienda:

```
input/market/INTC_si.csv
date,short_interest,close,avg_volume,days_to_cover
```

L'export non contiene la percentuale sul flottante, quindi il sotto-punteggio
**Mercato** non la usa: misura lo short interest contro la storia del titolo
stesso (scostamento dalla mediana dell'ultimo anno, direzione negli ultimi tre
mesi, giorni per ricoprire rispetto alla loro mediana). Cosi' un'azienda grande
e una piccola restano confrontabili, e un numero non pesa per il fatto di essere
grande. Un test lo verifica: la stessa serie moltiplicata per dieci da' lo
stesso punteggio.

### Percentuale sul circolante

Le azioni in circolazione non arrivano da Bloomberg: stanno in copertina a ogni
10-Q e 10-K, e la SEC le espone in un endpoint pubblico
(`companyconcept/.../dei/EntityCommonStockSharesOutstanding`). La pipeline le
scarica da sola per tutte le aziende, insieme ai Form 4.

Attenzione a come si chiama il numero: quello che esce e' lo short interest in
**percentuale delle azioni in circolazione**, non del flottante. Il flottante
esclude le azioni vincolate e quelle in mano a insider e grandi soci, quindi la
percentuale vera sul flottante e' un po' piu' alta. Per le grandi societa' la
differenza e' piccola, ma il sito scrive "azioni in circolazione" e non
"flottante", perche' e' quello che il dato dice davvero.

### Il confronto con i simili

Quando piu' aziende dello stesso universo hanno il file dello short interest, il
sotto-punteggio Mercato aggiunge un confronto: la percentuale sulle azioni in
circolazione di questa azienda contro la **mediana del suo gruppo**. Essere
shortati molto piu' dei simili toglie punti, molto meno li aggiunge, con un
tetto di otto. Servono almeno tre aziende confrontabili, altrimenti il confronto
non si fa.

E' la differenza fra "e' shortata piu' del solito" (confronto con la propria
storia) e "e' shortata piu' delle altre" (confronto con il settore). Le due cose
insieme dicono molto di piu' di ciascuna da sola.

Lo short interest nasce da FINRA e dalle borse, non e' un prodotto proprietario
Bloomberg: per questo i numeri stanno nel repo. Le revisioni delle stime e il
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
trovato al posto suo. Il controllo non costa richieste in piu': quel JSON serve
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

## Gli universi di titoli

`config.json` contiene due liste, e si aggiungono e si tolgono ticker
scrivendo dentro:

- **Semiconduttori e apparecchiature** / *Semiconductors and equipment*
  (20 titoli, riferimento `SMH`)
- **Grandi capitalizzazioni USA** / *US large caps* (20 titoli, riferimento `SPY`)

La divisione non è estetica. Il sotto-punteggio **Settore** confronta ogni
azienda con le altre dello stesso gruppo: mettere un produttore di chip e una
catena di supermercati nello stesso confronto darebbe un numero senza
significato. Per aggiungere un terzo gruppo basta copiare un blocco di
`universi` e cambiare nome, riferimento e lista.

Il primo giro dura una ventina di minuti: scarica i Form 4 di quaranta
aziende, e le più grandi ne depositano parecchi. Dal secondo in poi ne bastano
due o tre, perché i documenti già letti restano in cache e un documento
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
- `submissions` di EDGAR tiene le ultime mille registrazioni: per aziende molto
  attive e storici lunghi servono anche i file archiviati.
- Il rendimento anomalo usa un ETF di settore come riferimento, non un modello
  di rischio.
- Lo score non è una raccomandazione di investimento.

## Passo successivo

Collegare la dashboard a `data.json` al posto dei dati dimostrativi, e
aggiungere i Form 4 dei concorrenti per il sotto-punteggio di settore.

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
