"""Short interest da FINRA: i file pubblici, uno per data di regolamento.

FINRA pubblica due volte al mese lo short interest consolidato di tutti i titoli
quotati (NYSE, Nasdaq e le altre borse, più l'OTC), con la data di regolamento
del 15 e quella di fine mese: se cadono di sabato, di domenica o in un giorno
di festa, vale il giorno di Borsa prima. Ogni rilevazione è un file a sé,

    https://cdn.finra.org/equity/otcmarket/biweekly/shrtAAAAMMGG.csv

con i campi separati da `|`. Nonostante "otcmarket" nell'indirizzo, è il file
consolidato. Non serve nessuna chiave. Esce circa sette giorni di Borsa dopo la
data di regolamento, e per l'API FINRA tiene cinque anni.

Ogni file pesa circa 3 MB e ha più di ventimila righe, ma alla pipeline ne
servono duecento: in cache (cache/finra/) resta un file piccolo per ogni
rilevazione, con le sole righe delle aziende di config.json. Cinque anni sono
120 rilevazioni: si scaricano a rate, al massimo `files` richieste per giro,
dalla più recente alla più vecchia. Quando config.json cambia aziende, le
rilevazioni già in cache restano valide per le aziende che c'erano, e si
riscaricano una volta per prendere anche le nuove.

Il nome del file è la data di regolamento, che non si sa in anticipo per via
delle feste: si provano i giorni feriali a partire dal 15 (o dalla fine del
mese) andando indietro. Un file che non c'è risponde 403 o 404. "Questa
rilevazione non esiste" si scrive in cache solo se è vecchia abbastanza da
essere già uscita, e solo se nello stesso giro un altro file è arrivato: se
FINRA non risponde a niente, è un guasto, non un buco nei dati.
"""
import csv
import hashlib
import io
import json
import os
import time
from datetime import date, timedelta

URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{d}.csv"
YEARS = 5
FILES_PER_RUN = 60      # richieste per giro (riuscite o no)
PUBLISH_DAYS = 21       # entro questi giorni dalla data di regolamento il file può non essere ancora uscito
TRY_DAYS = 6            # quanti giorni indietro provare dal 15 o dalla fine del mese
# Niente email qui: l'indirizzo in SEC_UA è per la SEC, che lo chiede.
UA = "conviction-engine (progetto universitario; github.com/mpascale19/conviction-engine)"

# Il nome delle colonne nel file di oggi e, dopo la barra, in quelli di prima del 2019.
COLS = {"symbol": ("symbolcode", "issuesymbolidentifier"),
        "si": ("currentshortpositionquantity", "currentshortsharenumber"),
        "vol": ("averagedailyvolumequantity", "averageshortsharenumber"),
        "dtc": ("daystocoverquantity", "daystocovernumber"),
        "date": ("settlementdate",)}


# ------------------------------------------------------------------ calendario
def periods(today, years=YEARS):
    """Le rilevazioni da cercare, dalla più recente: [(chiave, [date da provare])].

    La chiave è "AAAA-MM-A" per quella del 15 e "AAAA-MM-B" per quella di fine
    mese. Le date da provare sono giorni feriali, dalla data teorica indietro.
    Le rilevazioni con la data teorica dopo oggi non ci sono ancora.
    """
    out = []
    y, m = today.year, today.month
    for _ in range(years * 12 + 1):
        end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        for key, top in ((f"{y:04d}-{m:02d}-B", end), (f"{y:04d}-{m:02d}-A", date(y, m, 15))):
            if top > today:
                continue
            days = [top - timedelta(days=k) for k in range(TRY_DAYS)]
            out.append((key, [d for d in days if d.weekday() < 5]))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    first = date(today.year - years, today.month, min(today.day, 28))
    return [p for p in out if p[1] and p[1][0] >= first]


# ------------------------------------------------------------------ un file
def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse(text, wanted):
    """Il testo di un file FINRA -> (data di regolamento, {ticker: [si, vol, dtc]}).

    Legge i nomi delle colonne dall'intestazione, così un cambio d'ordine non
    rompe niente; il separatore è `|` (o la virgola, se un giorno cambiasse).
    """
    text = text.lstrip("﻿")
    head = text.split("\n", 1)[0]
    sep = "|" if "|" in head else ","
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    try:
        names = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return None, {}
    idx = {}
    for k, opts in COLS.items():
        for o in opts:
            if o in names:
                idx[k] = names.index(o)
                break
    if not {"symbol", "si"} <= set(idx):
        raise ValueError(f"colonne inattese nel file FINRA: {head[:200]}")
    rows, settle = {}, None
    for r in reader:
        if len(r) <= max(idx.values()):
            continue
        sym = r[idx["symbol"]].strip().upper()
        if sym not in wanted:
            continue
        si = _num(r[idx["si"]])
        if si is None:
            continue
        rows[sym] = [si, _num(r[idx["vol"]]) if "vol" in idx else None,
                     _num(r[idx["dtc"]]) if "dtc" in idx else None]
        if settle is None and "date" in idx:
            settle = r[idx["date"]].strip()[:10] or None
    return settle, rows


def _download(day):
    """Il testo del file di quel giorno, o None se non c'è. Altri errori salgono."""
    import requests
    r = requests.get(URL.format(d=day.strftime("%Y%m%d")), headers={"User-Agent": UA}, timeout=60)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    return r.text


# ------------------------------------------------------------------ la serie
def _tag(tickers):
    return hashlib.sha1(",".join(sorted(tickers)).encode()).hexdigest()[:12]


def update(cache_dir, tickers, today=None, files=FILES_PER_RUN, offline=False, get=None):
    """Aggiorna la cache e restituisce ({ticker: [rilevazioni]}, stato).

    Una rilevazione è {"date", "si", "vol", "dtc"}, come quelle degli export
    manuali (market.load_short_interest), in ordine di data. Lo stato dice
    quante rilevazioni ci sono e quante mancano ancora. `get` sostituisce lo
    scaricamento nei test.
    """
    today = today or date.today()
    get = get or _download
    wanted = {t.upper() for t in tickers}
    tag = _tag(wanted)
    os.makedirs(cache_dir, exist_ok=True)
    left, found_now, nones, have, pending, head = files, 0, [], {}, 0, None
    for key, days in periods(today):
        path = os.path.join(cache_dir, f"{key}.json")
        old = None
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    old = json.load(f)
            except (OSError, ValueError):
                old = None
        # una rilevazione che non esiste non dipende dalle aziende; le altre sì
        if old is not None and (old.get("date") is None or old.get("tag") == tag or offline):
            have[key] = old
            continue
        if offline or left <= 0:
            if old is not None:
                have[key] = old           # vale per le aziende che c'erano
            pending += 1
            continue
        got, tried_all = None, True
        for d in days:
            if left <= 0:
                tried_all = False         # il tetto è finito a metà: si riprende al giro dopo
                break
            left -= 1
            text = get(d)
            if get is _download:
                time.sleep(0.2)
            if text is None:
                continue
            if head is None:              # per capire da remoto che cosa manda FINRA
                head = text.lstrip("\ufeff").split("\n", 1)[0][:300]
            _, rows = parse(text, wanted)
            # la data è quella del nome del file, che è la data di regolamento:
            # il campo nel file c'è, ma il suo formato è cambiato negli anni
            got = {"date": d.isoformat(), "tag": tag, "rows": rows}
            break
        if got is not None:
            found_now += 1
            have[key] = got
            with open(path, "w", encoding="utf-8") as f:
                json.dump(got, f)
            continue
        if old is not None:
            have[key] = old
        if not tried_all:
            pending += 1
        elif (today - days[0]).days > PUBLISH_DAYS:
            nones.append((key, path))     # provati tutti i giorni, ed è vecchia: non esiste
        # altrimenti è recente e non è ancora uscita: si riprova al giro dopo
    # "non esiste" si scrive solo se FINRA in questo giro ha risposto con dei file,
    # o li aveva già dati prima: altrimenti è FINRA che non risponde
    if found_now or any(v.get("date") for v in have.values()):
        for key, path in nones:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"date": None, "tag": tag, "rows": {}}, f)
            have[key] = {"date": None, "rows": {}}
    else:
        pending += len(nones)

    series = {}
    for v in have.values():
        if not v.get("date"):
            continue
        for t, (si, vol, dtc) in (v.get("rows") or {}).items():
            if t in wanted:
                series.setdefault(t, []).append({"date": v["date"], "si": si,
                                                 "vol": vol or 0.0, "dtc": dtc or 0.0})
    for t in series:
        series[t].sort(key=lambda r: r["date"])
    dates = sorted(v["date"] for v in have.values() if v.get("date"))
    status = {"readings": len(dates), "pending": pending,
              "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
              "companies": len(series), "downloaded": found_now, "head": head}
    return series, status
