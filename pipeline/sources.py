"""Fonti pubbliche e gratuite.

Prezzi : Yahoo Finance (due host), con stooq.com come riserva; nessuna chiave.
Insider: SEC EDGAR, submissions JSON + XML del Form 4.

La SEC chiede uno User-Agent con un contatto vero: imposta la variabile
d'ambiente SEC_UA, per esempio "Michele Pascale michele@example.com".
Le funzioni di parsing sono separate da quelle di rete, così si possono
testare senza connessione (vedi tests/test_offline.py).
"""
import csv
import io
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

SEC_UA = os.environ.get("SEC_UA", "Conviction Engine (studente) nome@example.com")
SEC_UA_IS_DEFAULT = "nome@example.com" in SEC_UA
# La SEC vuole un contatto vero nello User-Agent e rifiuta (403) le richieste
# che le sembrano automatiche: queste intestazioni sono quelle di un client
# normale, più il contatto richiesto.
HEAD = {"User-Agent": SEC_UA,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"}
# Yahoo rifiuta le richieste senza un User-Agent riconoscibile.
WEB = {"User-Agent": "Mozilla/5.0 (compatible; ConvictionEngine/1.0; progetto didattico)",
       "Accept": "application/json, text/plain, */*"}
_last_call = 0.0


class SourceError(RuntimeError):
    """Una fonte non ha risposto come doveva."""



def _get(url, headers=None, tries=3):
    """GET con pausa fra le richieste: la SEC accetta al massimo 10 req/s."""
    global _last_call
    err = None
    for n in range(tries):
        pause = 0.15 - (time.time() - _last_call)
        if pause > 0:
            time.sleep(pause)
        try:
            r = requests.get(url, headers=headers or HEAD, timeout=30)
            _last_call = time.time()
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429, 503):
                time.sleep(2 ** n)
                err = f"{r.status_code} su {url}"
                continue
            r.raise_for_status()
        except requests.RequestException as e:  # rete che cade, DNS, timeout
            err = str(e)
            time.sleep(2 ** n)
    raise RuntimeError(f"richiesta fallita: {err}")


# ----------------------------------------------------------------- prezzi
def parse_prices_csv(text):
    """CSV di stooq -> [{'d': 'YYYY-MM-DD', 'c': float, 'v': int}, ...]"""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        close = (row.get("Close") or "").strip()
        if not close or close in ("N/D", "null"):
            continue
        try:
            out.append({
                "d": row["Date"],
                "c": float(close),
                "v": int(float(row.get("Volume") or 0)),
            })
        except ValueError:
            continue
    out.sort(key=lambda r: r["d"])
    return out


def parse_prices_yahoo(payload):
    """Risposta del grafico di Yahoo -> stessa forma di parse_prices_csv."""
    res = (payload.get("chart") or {}).get("result")
    if not res:
        err = ((payload.get("chart") or {}).get("error") or {}).get("description")
        raise SourceError(err or "risposta senza dati")
    res = res[0]
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes = q.get("close") or []
    vols = q.get("volume") or [0] * len(ts)
    out = []
    for t, c, v in zip(ts, closes, vols):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"d": d, "c": round(float(c), 4), "v": int(v or 0)})
    out.sort(key=lambda r: r["d"])
    return out


HIST_YEARS = 10     # quanta storia di prezzi: dieci anni dal 23/11/2026 (prima cinque)


def _prices_yahoo(ticker, years=HIST_YEARS, host="query1"):
    url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?range={years}y&interval=1d")
    return parse_prices_yahoo(_get(url, headers=WEB).json())


def _prices_stooq(ticker, market="us"):
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.{market}&i=d"
    return parse_prices_csv(_get(url, headers=WEB).text)


def prices(ticker, market="us", min_rows=20):
    """Prova le fonti in ordine e restituisce (righe, nome della fonte).

    Yahoo per primo: stooq spesso blocca le richieste che arrivano dai server
    di GitHub, e in quel caso risponde 200 con una pagina che non è un CSV.
    """
    tried = []
    for name, fn in (("yahoo", lambda: _prices_yahoo(ticker)),
                     ("yahoo-2", lambda: _prices_yahoo(ticker, host="query2")),
                     ("stooq", lambda: _prices_stooq(ticker, market))):
        try:
            rows = fn()
        except Exception as e:
            tried.append(f"{name}: {e}")
            continue
        # stooq restituisce tutta la storia, Yahoo quella chiesta: stessa
        # finestra per tutti, così un'azienda non ha più passato di un'altra
        cut = (date.today() - timedelta(days=int(HIST_YEARS * 365.25) + 7)).isoformat()
        rows = [r for r in rows if r["d"] >= cut]
        if len(rows) >= min_rows:
            return rows, name
        tried.append(f"{name}: solo {len(rows)} righe utili")
    raise SourceError("nessuna fonte di prezzi ha risposto -> " + "; ".join(tried))


# ----------------------------------------------------------------- EDGAR
_SUBS = {}


def submissions(cik):
    """Il JSON dei depositi di un CIK, scaricato una volta sola per giro."""
    cik = str(cik).zfill(10)
    if cik not in _SUBS:
        _SUBS[cik] = _get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
        while len(_SUBS) > 2:             # un'azienda alla volta: non teniamo tutto in RAM
            _SUBS.pop(next(iter(_SUBS)))
    return _SUBS[cik]


_TAB = {}

# CIK precedenti di un'azienda: {cik attuale (10 cifre): [cik precedenti]}.
# Quando un'azienda cambia sede o capogruppo deposita con un CIK nuovo, e tutta la
# storia resta sotto quello vecchio: ExxonMobil, trasferita in Texas il 1° luglio
# 2026, deposita come 0002115436, ma i suoi Form 4 e 8-K di prima stanno sotto
# 0000034088. Lo riempie build.py da config.json ("cik_predecessors").
PREDECESSORS = {}


def filings_table(cik, since=None):
    """I depositi dell'azienda e quelli dei suoi CIK precedenti, in un elenco solo.

    Ogni riga porta in `_cik` il CIK che l'ha depositata: serve a costruire
    l'indirizzo del documento, che sta nella cartella di quel CIK.
    Restituisce (colonne, data da cui l'elenco è completo).
    """
    cik = str(cik).zfill(10)
    cols, covered = _table_one(cik, since)
    cols = {k: list(v) for k, v in cols.items()}    # copia: l'originale sta in cache
    for old in PREDECESSORS.get(cik, []):
        try:
            more, cov2 = _table_one(str(old).zfill(10), since)
        except Exception:
            continue                      # senza il predecessore l'elenco parte dove parte
        n, m = len(cols.get("filingDate") or []), len(more.get("filingDate") or [])
        for k in set(cols) | set(more):
            cols[k] = (list(cols.get(k) or []) + [""] * n)[:n] + (list(more.get(k) or []) + [""] * m)[:m]
        if cov2 and (covered is None or cov2 < covered):
            covered = cov2
    order = sorted(range(len(cols.get("filingDate") or [])),
                   key=lambda i: cols["filingDate"][i], reverse=True)
    return {k: [v[i] for i in order] for k, v in cols.items()}, covered


ARCHIVE_DIR = None   # dove tenere gli archivi dei depositi fra un giro e l'altro (lo imposta build.py)
ARCHIVE_COLS = ("accessionNumber", "filingDate", "form", "primaryDocument", "items")


def _archive(f):
    """Un file d'archivio dei depositi, dalla cache su disco se c'è.

    Le aziende grandi hanno decine di archivi (Goldman Sachs deposita migliaia
    di prospetti l'anno), e senza cache si riscaricherebbero tutti a ogni giro.
    Il nome del file in cache contiene le date e il numero di depositi che la
    SEC dichiara per quell'archivio: se la SEC lo rifà, il nome cambia e
    l'archivio si riscarica.
    """
    name = f["name"]
    path = None
    if ARCHIVE_DIR:
        tag = f"{name[:-5] if name.endswith('.json') else name}_{f.get('filingFrom')}_" \
              f"{f.get('filingTo')}_{f.get('filingCount')}.json"
        path = os.path.join(ARCHIVE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "", tag))
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                pass
    more = _get(f"https://data.sec.gov/submissions/{name}").json()
    # solo le colonne che la pipeline legge: un terzo del peso, e la cache di
    # GitHub Actions ha un tetto per repository
    more = {k: v for k, v in more.items() if k in ARCHIVE_COLS}
    if path:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(more, fh)
    return more


def _table_one(cik, since=None):
    """Tutti i depositi del CIK fino a `since`, anche quelli negli archivi.

    Il JSON principale tiene solo gli ultimi mille moduli di ogni tipo: per
    un'azienda con molti Form 4 sono pochi mesi (per Google da novembre 2025).
    I più vecchi stanno in file d'archivio elencati in `filings.files`, con le
    date che coprono; si scaricano solo quelli che servono ad arrivare a
    `since`, una richiesta ciascuno.

    Restituisce (colonne, data da cui l'elenco è completo).
    """
    cik = str(cik).zfill(10)
    key = (cik, since)
    if key in _TAB:
        return _TAB[key]
    j = submissions(cik)
    rec = (j.get("filings") or {}).get("recent") or {}
    cols = {k: list(v) for k, v in rec.items() if isinstance(v, list)}
    cols["_cik"] = [cik] * len(cols.get("filingDate") or [])
    dates = cols.get("filingDate") or []
    covered = min(dates) if dates else None
    if since and (covered is None or covered > since):
        for f in (j.get("filings") or {}).get("files") or []:
            if (f.get("filingTo") or "9999") < since:
                continue                  # un archivio tutto più vecchio di quanto serve
            try:
                more = _archive(f)
            except Exception:
                break                     # senza questo archivio l'elenco si ferma qui
            n = len(more.get("accessionNumber") or [])
            more = dict(more, _cik=[cik] * n)
            for k in cols:                # colonne mancanti o corte: vuote, per restare allineate
                cols[k] += (list(more.get(k) or []) + [""] * n)[:n]
            got = [d for d in (more.get("filingDate") or []) if d]
            if got:
                covered = min([covered] + got) if covered else min(got)
    # dal più recente al più vecchio, qualunque sia l'ordine degli archivi
    order = sorted(range(len(cols.get("filingDate") or [])),
                   key=lambda i: cols["filingDate"][i], reverse=True)
    cols = {k: [v[i] for i in order] for k, v in cols.items()}
    _TAB[key] = (cols, covered)
    while len(_TAB) > 4:
        _TAB.pop(next(iter(_TAB)))
    return cols, covered


def recent_8k(cik, since):
    """Gli 8-K depositati dal giorno `since` in poi, con i loro codici item.

    L'8-K è il modulo con cui un'azienda americana dichiara un fatto rilevante
    entro quattro giorni lavorativi. I codici item sono fissati dalla SEC, quindi
    non c'e' niente da interpretare: "4.02" vuol dire sempre che i conti passati
    non sono più affidabili. Legge il JSON dei depositi già scaricato per il
    CIK (e, se `since` è più vecchio di quanto copre, i suoi archivi).
    """
    rec, _ = filings_table(cik, since)
    forms, dates = rec.get("form") or [], rec.get("filingDate") or []
    items, accs = rec.get("items") or [], rec.get("accessionNumber") or []
    out = []
    for i, form in enumerate(forms):
        if not str(form).startswith("8-K"):
            continue
        d = dates[i] if i < len(dates) else ""
        if not d or d < since:
            continue
        raw = items[i] if i < len(items) else ""
        codes = [c.strip() for c in str(raw).split(",") if c.strip()]
        out.append({"date": d, "items": codes,
                    "acc": accs[i] if i < len(accs) else ""})
    out.sort(key=lambda x: x["date"])
    return out


def confirms(cik, ticker):
    """La SEC conferma che quel CIK ha davvero quel ticker?

    La mappa company_tickers.json ogni tanto sbaglia una riga (ADI -> Eli
    Lilly, visto il 16/11/2026). Il JSON dei depositi elenca i ticker
    dell'azienda: è la prova del nove, e lo scarichiamo comunque subito dopo.
    Restituisce (ok, nome, ticker elencati).
    """
    j = submissions(cik)
    tk = [str(t).upper() for t in (j.get("tickers") or [])]
    name = j.get("name") or j.get("entityName") or ticker   # il JSON dei depositi usa "name"
    return (not tk or ticker.upper() in tk), name, tk


_TICKERS = None


def cik_for(ticker, fallback_cik=None):
    """Ticker -> (CIK a 10 cifre, nome, come l'ha trovato).

    Tre strade, perché www.sec.gov a volte risponde 403 alle richieste che
    arrivano dai server di GitHub: la mappa ufficiale, la ricerca di EDGAR in
    formato atom, e infine il CIK scritto a mano in config.json.
    """
    tried = []

    def verified(cik, how):
        """Accetta un CIK solo se la SEC conferma il ticker."""
        cik = str(cik).zfill(10)
        ok, name, tk = confirms(cik, ticker)
        if ok:
            return cik, name, how
        tried.append(f"{how}: il CIK {cik} è di {name} "
                     f"(ticker {', '.join(tk) or '-'}), non di {ticker}")
        return None

    try:
        global _TICKERS
        if _TICKERS is None:              # un file da un megabyte: una volta per giro, non per azienda
            _TICKERS = _get("https://www.sec.gov/files/company_tickers.json").json()
        data = _TICKERS
        hit = None
        for row in data.values():
            if str(row.get("ticker", "")).upper() == ticker.upper():
                hit = row
                break
        if hit is None:
            tried.append("company_tickers.json: il ticker non c'è")
        else:
            got = verified(hit["cik_str"], "company_tickers.json")
            if got:
                return got
    except Exception as e:
        tried.append(f"company_tickers.json: {e}")

    try:
        url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
               f"{ticker}&type=4&dateb=&owner=include&count=1&output=atom")
        txt = _get(url).text
        m = re.search(r"<CIK>(\d+)</CIK>", txt) or re.search(r"CIK=(\d{10})", txt)
        nm = re.search(r"<conformed-name>([^<]+)</conformed-name>", txt)
        if m:
            got = verified(m.group(1), "browse-edgar")
            if got:
                return got
        else:
            tried.append("browse-edgar: nessun CIK nella risposta")
    except Exception as e:
        tried.append(f"browse-edgar: {e}")

    if fallback_cik:                      # scritto a mano: ha sempre ragione
        cik = str(fallback_cik).strip().zfill(10)
        try:
            return cik, confirms(cik, ticker)[1], "config.json"
        except Exception:
            return cik, ticker, "config.json"
    raise SourceError("CIK non trovato -> " + "; ".join(tried) +
                      " | rimedio: scrivi il numero nel campo \"cik\" di config.json")


def parse_shares(payload):
    """Dal JSON della SEC: le azioni in circolazione più recenti.

    Il dato sta in copertina a ogni 10-Q e 10-K ("EntityCommonStockSharesOutstanding").
    Prendiamo la rilevazione con la data di fine periodo più recente.
    """
    units = (payload.get("units") or {})
    rows = []
    for vals in units.values():
        for v in vals:
            n, end = v.get("val"), v.get("end") or v.get("fy")
            if isinstance(n, (int, float)) and n > 0 and end:
                rows.append((str(end), float(n)))
    if not rows:
        return None, None
    rows.sort()
    return rows[-1][1], rows[-1][0]


def shares_outstanding(cik):
    """Azioni in circolazione di un CIK: (numero, data). Non solleva: (None, None)."""
    url = ("https://data.sec.gov/api/xbrl/companyconcept/"
           f"CIK{str(cik).zfill(10)}/dei/EntityCommonStockSharesOutstanding.json")
    try:
        return parse_shares(_get(url).json())
    except Exception:
        return None, None


def raw_xml_url(cik, accession, primary_doc):
    """L'URL del Form 4 in XML grezzo (senza il foglio di stile XSL)."""
    doc = re.sub(r"^xsl[^/]+/", "", primary_doc)
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{doc}")


def form4_filings(cik, months=24):
    """Elenco dei Form 4 depositati negli ultimi `months` mesi, dal più recente."""
    cutoff = (date.today() - timedelta(days=31 * months)).isoformat()
    r, _ = filings_table(cik, cutoff)
    out = []
    owners = r.get("_cik") or [cik] * len(r["form"])
    for form, acc, doc, fdate, who in zip(r["form"], r["accessionNumber"],
                                          r["primaryDocument"], r["filingDate"], owners):
        if form != "4" or fdate < cutoff:
            continue
        out.append({"accession": acc, "date": fdate,
                    "url": raw_xml_url(who or cik, acc, doc)})
    return out


def _same_cik(a, b):
    try:
        return int(str(a).strip()) == int(str(b).strip())
    except (TypeError, ValueError):
        return False


def parse_form4(xml_text, issuer_cik=None):
    """XML del Form 4 -> lista di operazioni già normalizzate.

    Il flag del piano 10b5-1 si legge dalla nota a piè di pagina agganciata
    alla singola operazione; se quella riga non ha note, vale quello che dice
    il documento nel suo insieme.

    `issuer_cik`: l'azienda di cui stiamo leggendo gli insider. L'elenco dei
    depositi di un CIK su EDGAR contiene anche i Form 4 che quell'azienda
    deposita come *azionista* di altre società: Bank of America che compra
    quote di fondi, Coca-Cola che vende la partecipazione in un imbottigliatore,
    Intel che cede azioni di un'altra società. Sono operazioni su titoli di altri,
    e contarle come insider dell'azienda le metteva nel punteggio (una vendita
    di 2,4 miliardi registrata come vendita discrezionale di KO). Se l'emittente
    del documento non è l'azienda, il documento non dice niente su di lei.
    """
    clean = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", xml_text, count=5)
    x = ET.fromstring(clean)
    if issuer_cik is not None:
        doc_issuer = x.findtext("issuer/issuerCik")
        mine = issuer_cik if isinstance(issuer_cik, (list, tuple, set)) else [issuer_cik]
        if doc_issuer is not None and not any(_same_cik(doc_issuer, c) for c in mine):
            return None          # None, non []: è un documento di un altro, non un documento vuoto
    owner = (x.findtext("reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    rel = x.find("reportingOwner/reportingOwnerRelationship")
    truthy = ("1", "true")
    is_dir = bool(rel is not None and (rel.findtext("isDirector") or "").strip() in truthy)
    is_off = bool(rel is not None and (rel.findtext("isOfficer") or "").strip() in truthy)
    title = (rel.findtext("officerTitle") if rel is not None else "") or ""
    role = title.strip() or ("Director" if is_dir else ("Officer" if is_off else "10% owner"))
    low = xml_text.lower().replace("\u2011", "-")
    doc_plan = "10b5-1" in low
    # Le note sono per operazione: in un documento una vendita puo\u2019
    # essere programmata e le altre no.
    notes = {f.get("id"): (f.text or "").lower()
             for f in x.findall("footnotes/footnote")}
    plan_notes = {i for i, txt in notes.items() if "10b5-1" in txt}

    out = []
    for t in x.findall("nonDerivativeTable/nonDerivativeTransaction"):
        refs = [e.get("id") for e in t.iter() if e.tag == "footnoteId"]
        if refs:
            plan = any(r in plan_notes for r in refs)
        else:
            # nessuna nota su questa riga: vale quello che dice il documento
            plan = doc_plan and not plan_notes
        code = (t.findtext("transactionCoding/transactionCode") or "").strip()
        amt = t.find("transactionAmounts")
        if amt is None:
            continue
        shares = _num(amt.findtext("transactionShares/value"))
        price = _num(amt.findtext("transactionPricePerShare/value"))
        ad = (amt.findtext("transactionAcquiredDisposedCode/value") or "").strip()
        out.append({
            "date": (t.findtext("transactionDate/value") or "").strip(),
            "who": owner,
            "role": role,
            "code": code,
            "ad": ad,
            "shares": shares,
            "price": price,
            "value": shares * price,
            "plan": plan,
            "is_senior": is_off,
            "is_director": is_dir,
        })
    return out


def _num(s):
    try:
        return float((s or "0").strip())
    except ValueError:
        return 0.0


RECENT_DAYS = 120   # i Form 4 di questi ultimi giorni si scaricano sempre, tetto o no


def _exhausted(budget):
    """Il tetto del giro è finito? Conta i documenti e, se c'è, anche l'orologio."""
    if budget is None:
        return False
    return budget.get("left", 0) <= 0 or time.time() > budget.get("until", float("inf"))


def insider_transactions(cik, months=24, limit=None, cache_dir=None, budget=None, status=None):
    """Scarica e normalizza i Form 4. Una richiesta per documento.

    Se i primi documenti falliscono tutti si ferma subito: vuol dire che
    l'archivio ci sta rifiutando, e non ha senso provare cento volte.

    `budget`: un dizionario {"left": n} condiviso fra tutte le aziende del giro,
    con quanti documenti nuovi si possono ancora scaricare. Cinque anni di Form 4
    di duecento aziende sono più di centomila documenti: tutti insieme
    farebbero durare un giro molte ore, e il giro dell'ora dopo lo
    annullerebbe. Così la storia si riempie a rate, dal più recente al più
    vecchio, e i documenti già letti restano in cache. Quelli recenti, che
    servono al punteggio di oggi, non aspettano: quelli degli ultimi
    RECENT_DAYS giorni si scaricano sempre e non contano nel tetto. Così
    un'azienda appena aggiunta ha subito la voce insider di oggi, e la sua
    storia arriva nei giri successivi. `budget` può avere anche "until", un
    orario (time.time()) dopo cui la storia vecchia aspetta il giro dopo.

    `status`, se passato, riceve {"complete": bool, "missing": n}: se lo
    storico di questa azienda è completo o sta ancora crescendo.
    """
    rows, done, failed, cached, foreign, own, missing = [], 0, 0, 0, 0, 0, 0
    issuers = [cik] + list(PREDECESSORS.get(str(cik).zfill(10), []))
    filings = form4_filings(cik, months)
    recent_from = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    if status is not None:
        status.update(complete=True, missing=0)
    if not filings:
        return rows
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    # Il limite conta solo i documenti dell'azienda stessa. Quelli che
    # l'azienda deposita come azionista di altre società si saltano senza
    # contarli: per Bank of America erano la maggior parte dei primi 120, e
    # contandoli gli insider veri finivano fuori dal limite.
    for f in filings:
        if limit and own >= limit:
            break
        # un documento depositato non cambia più: una volta letto si tiene
        cf = os.path.join(cache_dir, f["accession"] + ".json") if cache_dir else None
        if cf and os.path.exists(cf):
            try:
                with open(cf, encoding="utf-8") as fh:
                    got = json.load(fh)
                cached += 1
                if got is None:
                    foreign += 1
                else:
                    rows += got
                    own += 1
                continue
            except Exception:
                pass
        old = f["date"] < recent_from
        if old and _exhausted(budget):
            missing += 1                  # tocca al prossimo giro
            continue
        try:
            if budget is not None and old:
                budget["left"] -= 1
            got = parse_form4(_get(f["url"]).text, issuer_cik=issuers)
            done += 1
            if got is None:
                foreign += 1
            else:
                rows += got
                own += 1
            if cf:
                with open(cf, "w", encoding="utf-8") as fh:
                    json.dump(got, fh)
        except Exception as e:
            failed += 1
            print(f"  ! salto {f['accession']}: {e}")
            if done == 0 and failed >= 3:
                raise SourceError(
                    f"i primi {failed} documenti dell'archivio hanno dato errore "
                    f"({e}): www.sec.gov sta rifiutando le richieste")
    rows.sort(key=lambda r: r["date"])
    if status is not None:
        status.update(complete=(missing == 0 and failed == 0), missing=missing + failed)
    print(f"  {done} documenti scaricati, {cached} dalla cache, {failed} saltati, "
          f"{foreign} di altri emittenti esclusi"
          + (f", {missing} rimandati al prossimo giro" if missing else ""))
    return rows
