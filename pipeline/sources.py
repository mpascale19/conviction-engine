"""Fonti pubbliche e gratuite.

Prezzi : stooq.com, CSV, nessuna chiave.
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
# normale, piu' il contatto richiesto.
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


def _prices_yahoo(ticker, years=2, host="query1"):
    url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?range={years}y&interval=1d")
    return parse_prices_yahoo(_get(url, headers=WEB).json())


def _prices_stooq(ticker, market="us"):
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.{market}&i=d"
    return parse_prices_csv(_get(url, headers=WEB).text)


def prices(ticker, market="us", min_rows=20):
    """Prova le fonti in ordine e restituisce (righe, nome della fonte).

    Yahoo per primo: stooq spesso blocca le richieste che arrivano dai server
    di GitHub, e in quel caso risponde 200 con una pagina che non e' un CSV.
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


def confirms(cik, ticker):
    """La SEC conferma che quel CIK ha davvero quel ticker?

    La mappa company_tickers.json ogni tanto sbaglia una riga (ADI -> Eli
    Lilly, visto il 16/11/2026). Il JSON dei depositi elenca i ticker
    dell'azienda: e' la prova del nove, e lo scarichiamo comunque subito dopo.
    Restituisce (ok, nome, ticker elencati).
    """
    j = submissions(cik)
    tk = [str(t).upper() for t in (j.get("tickers") or [])]
    name = j.get("name") or j.get("entityName") or ticker   # il JSON dei depositi usa "name"
    return (not tk or ticker.upper() in tk), name, tk


def cik_for(ticker, fallback_cik=None):
    """Ticker -> (CIK a 10 cifre, nome, come l'ha trovato).

    Tre strade, perche' www.sec.gov a volte risponde 403 alle richieste che
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
        tried.append(f"{how}: il CIK {cik} e' di {name} "
                     f"(ticker {', '.join(tk) or '-'}), non di {ticker}")
        return None

    try:
        data = _get("https://www.sec.gov/files/company_tickers.json").json()
        hit = None
        for row in data.values():
            if str(row.get("ticker", "")).upper() == ticker.upper():
                hit = row
                break
        if hit is None:
            tried.append("company_tickers.json: il ticker non c'e'")
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
    """Dal JSON della SEC: le azioni in circolazione piu' recenti.

    Il dato sta in copertina a ogni 10-Q e 10-K ("EntityCommonStockSharesOutstanding").
    Prendiamo la rilevazione con la data di fine periodo piu' recente.
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
    """Elenco dei Form 4 depositati negli ultimi `months` mesi."""
    j = submissions(cik)
    r = j["filings"]["recent"]
    cutoff = (date.today() - timedelta(days=31 * months)).isoformat()
    out = []
    for form, acc, doc, fdate in zip(r["form"], r["accessionNumber"],
                                     r["primaryDocument"], r["filingDate"]):
        if form != "4" or fdate < cutoff:
            continue
        out.append({"accession": acc, "date": fdate,
                    "url": raw_xml_url(cik, acc, doc)})
    return out


def parse_form4(xml_text):
    """XML del Form 4 -> lista di operazioni già normalizzate.

    Il flag del piano 10b5-1 si legge dalla nota a piè di pagina agganciata
    alla singola operazione; se quella riga non ha note, vale quello che dice
    il documento nel suo insieme.
    """
    clean = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", xml_text, count=5)
    x = ET.fromstring(clean)
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


def insider_transactions(cik, months=24, limit=None, cache_dir=None):
    """Scarica e normalizza i Form 4. Una richiesta per documento.

    Se i primi documenti falliscono tutti si ferma subito: vuol dire che
    l'archivio ci sta rifiutando, e non ha senso provare cento volte.
    """
    rows, done, failed, cached = [], 0, 0, 0
    filings = form4_filings(cik, months)
    if not filings:
        return rows
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    for f in (filings[:limit] if limit else filings):
        # un documento depositato non cambia piu': una volta letto si tiene
        cf = os.path.join(cache_dir, f["accession"] + ".json") if cache_dir else None
        if cf and os.path.exists(cf):
            try:
                with open(cf, encoding="utf-8") as fh:
                    rows += json.load(fh)
                cached += 1
                continue
            except Exception:
                pass
        try:
            got = parse_form4(_get(f["url"]).text)
            rows += got
            done += 1
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
    print(f"  {done} documenti scaricati, {cached} dalla cache, {failed} saltati")
    return rows
