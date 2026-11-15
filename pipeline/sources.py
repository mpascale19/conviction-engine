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
from datetime import date, datetime, timedelta

import requests

SEC_UA = os.environ.get("SEC_UA", "Conviction Engine (studente) nome@example.com")
HEAD = {
    "User-Agent": "Michele Pascale pascalem368@gmail.com"
}
_last_call = 0.0


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
            if r.status_code in (429, 503):
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


def prices(ticker, market="us"):
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.{market}&i=d"
    return parse_prices_csv(_get(url, headers={"User-Agent": SEC_UA}).text)


# ----------------------------------------------------------------- EDGAR
def cik_for(ticker):
    """Mappa ufficiale ticker -> CIK. Restituisce (cik a 10 cifre, nome)."""
    data = _get("https://www.sec.gov/files/company_tickers.json").json()
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10), row["title"]
    raise LookupError(f"ticker non trovato su EDGAR: {ticker}")


def raw_xml_url(cik, accession, primary_doc):
    """L'URL del Form 4 in XML grezzo (senza il foglio di stile XSL)."""
    doc = re.sub(r"^xsl[^/]+/", "", primary_doc)
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{doc}")


def form4_filings(cik, months=24):
    """Elenco dei Form 4 depositati negli ultimi `months` mesi."""
    j = _get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
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

    Il flag del piano 10b5-1 si legge dove c'è (le versioni recenti hanno
    una casella dedicata) e, in mancanza, dalle note a piè di pagina: il
    testo "10b5-1" compare comunque nel documento.
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
    plan = "10b5-1" in xml_text.lower().replace("‑", "-")

    out = []
    for t in x.findall("nonDerivativeTable/nonDerivativeTransaction"):
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


def insider_transactions(cik, months=24, limit=None):
    """Scarica e normalizza i Form 4. Una richiesta per documento."""
    rows = []
    filings = form4_filings(cik, months)
    for f in filings[:limit] if limit else filings:
        try:
            rows += parse_form4(_get(f["url"]).text)
        except Exception as e:                      # un documento rotto non ferma il resto
            print(f"  ! salto {f['accession']}: {e}")
    rows.sort(key=lambda r: r["date"])
    return rows
