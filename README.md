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
