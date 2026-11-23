"""Alpha Vantage oltre alle trascrizioni: calendario, elenco dei titoli, utili contro attese.

Tre richieste che costano poco e servono al progetto:

- **EARNINGS_CALENDAR**: con una richiesta sola, le prossime trimestrali di tutte
  le aziende quotate. Si chiede una volta al giorno. Il sito mostra la data della
  prossima, e le date passate dicono quando rileggere gli utili di un'azienda.
- **LISTING_STATUS**: l'elenco dei titoli quotati oggi. Una richiesta alla
  settimana. Un ticker dell'universo che non c'è più diventa un avviso sul sito,
  invece di un buco nei dati scoperto per caso (è successo con BK, diventata BNY
  il 21 maggio 2026). L'elenco dei titoli *tolti* (state=delisted) non si chiede:
  il 23 novembre 2026 Alpha Vantage rispondeva `{}`, anche al suo esempio
  ufficiale, e quella risposta vuota aveva fatto fallire il primo giro.
- **EARNINGS**: per ogni azienda, trimestre per trimestre, l'utile per azione
  annunciato, quello che si aspettavano gli analisti e la differenza. Una
  richiesta per azienda; la prima volta per tutte, poi solo dopo ogni nuova
  trimestrale.

La chiave gratuita dà 25 richieste al giorno, le stesse delle trascrizioni. Per
questo tutto passa da un posto solo, il workflow giornaliero delle trascrizioni
(pipeline/trascrizioni.py), che chiama `update()` prima di scaricare le call:
prima l'elenco dei titoli se ha più di una settimana, poi il calendario, poi al
massimo `per_day` aziende per gli utili, e il resto alle trascrizioni. Se una di
queste richieste va storta, il giro lo scrive nel riepilogo e va avanti con le
altre: le trascrizioni non devono mai restare senza per colpa di questa parte.

I dati restano nella cache di GitHub Actions (cartella cache-alphavantage), non
nel repository: sono dati di Alpha Vantage. Il giro orario del sito li legge da
lì con `load()`, e nelle schede finiscono solo i numeri che il sito mostra.
"""
import csv
import io
import json
import os
from datetime import date, timedelta

AV = "https://www.alphavantage.co/query"
DIR = "cache-alphavantage"
LISTING_DAYS = 7          # ogni quanto rileggere l'elenco dei titoli
LISTING_MIN_SHARE = .5    # un elenco con meno della metà dell'universo è una risposta sbagliata, non 100 titoli spariti
EARNINGS_PER_DAY = 12     # quante aziende al giorno per gli utili, al massimo
RETRY_DAYS = 3            # dopo una trimestrale, per quanti giorni riprovare se il dato non c'è ancora
REPORT_LOOKBACK = 120     # le trimestrali degli ultimi 120 giorni fanno rileggere gli utili
KEEP_QUARTERS = 32        # otto anni di trimestri: la prova all'indietro ne usa cinque
SURPRISE_DAYS = 100       # una trimestrale "conta" per i 100 giorni dopo l'annuncio


class Limit(Exception):
    """Alpha Vantage dice che le richieste di oggi sono finite (o che la chiave non va)."""


LIMIT_WORDS = ("rate limit", "requests per day", "premium", "frequency", "25 requests", "apikey")


def limit_note(payload):
    """Il messaggio di Alpha Vantage in una risposta JSON, e Limit se dice che oggi è finita.

    Alpha Vantage risponde sempre 200: l'errore sta nel testo, nei campi
    Information, Note o Error Message.
    """
    if not isinstance(payload, dict):
        return ""
    note = " ".join(str(payload.get(k, "")) for k in ("Information", "Note", "Error Message")).strip()
    if any(w in note.lower() for w in LIMIT_WORDS):
        raise Limit(note[:300])
    return note[:200]


def _default_get(params):
    import requests
    r = requests.get(AV, params=params, timeout=60)
    r.raise_for_status()
    return r.text


def request(params, key, get=None):
    """Il testo della risposta. `get(params) -> testo` si può sostituire nei test."""
    return (get or _default_get)(dict(params, apikey=key))


# ------------------------------------------------------------------ lettura delle risposte
def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None          # NaN -> None


def parse_csv(text):
    """CSV -> righe. Un JSON al posto del CSV è un messaggio d'errore."""
    s = (text or "").lstrip()
    if s.startswith("{"):
        note = limit_note(json.loads(s))
        raise ValueError(note or f"nessun CSV, Alpha Vantage ha risposto {s[:80]!r}")
    return list(csv.DictReader(io.StringIO(s)))


def parse_listing(text, wanted):
    """LISTING_STATUS -> {ticker: {name, exchange, ipo, delisted}} per i soli ticker voluti."""
    wanted = set(wanted)
    out = {}
    for r in parse_csv(text):
        t = (r.get("symbol") or "").strip().upper()
        if t in wanted:
            d = (r.get("delistingDate") or "").strip()
            out[t] = {"name": (r.get("name") or "").strip(), "exchange": (r.get("exchange") or "").strip(),
                      "ipo": (r.get("ipoDate") or "").strip() or None,
                      "delisted": d if d and d.lower() != "null" else None}
    return out


def parse_calendar(text, wanted):
    """EARNINGS_CALENDAR -> {ticker: [{date, fiscal_end, estimate}]} in ordine di data."""
    wanted = set(wanted)
    out = {}
    for r in parse_csv(text):
        t = (r.get("symbol") or "").strip().upper()
        d = (r.get("reportDate") or "").strip()
        if t in wanted and len(d) == 10:
            out.setdefault(t, []).append({"date": d, "fiscal_end": (r.get("fiscalDateEnding") or "").strip() or None,
                                          "estimate": _num(r.get("estimate"))})
    for v in out.values():
        v.sort(key=lambda x: x["date"])
    return out


def parse_earnings(text):
    """EARNINGS -> [{fiscal_end, date, eps, est, sp, time}] dal più vecchio, o [] se non c'è niente.

    `sp` è la sorpresa in percento: quella di Alpha Vantage se c'è, altrimenti
    la si calcola da utile e attese. Senza attese non c'è sorpresa.
    """
    payload = json.loads(text) if isinstance(text, str) else text
    limit_note(payload)
    out = []
    for r in (payload or {}).get("quarterlyEarnings") or []:
        d = (r.get("reportedDate") or "").strip()
        if len(d) != 10:
            continue
        eps, est = _num(r.get("reportedEPS")), _num(r.get("estimatedEPS"))
        sp = _num(r.get("surprisePercentage"))
        if sp is None and eps is not None and est:
            sp = (eps - est) / abs(est) * 100
        out.append({"fiscal_end": (r.get("fiscalDateEnding") or "").strip() or None, "date": d,
                    "eps": eps, "est": est, "sp": round(sp, 2) if sp is not None else None,
                    "time": (r.get("reportTime") or "").strip() or None})
    out.sort(key=lambda x: x["date"])
    return out[-KEEP_QUARTERS:]


# ------------------------------------------------------------------ cache
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _paths(d):
    return {"state": os.path.join(d, "stato.json"), "listing": os.path.join(d, "titoli.json"),
            "calendar": os.path.join(d, "calendario.json"), "earnings": os.path.join(d, "utili")}


def earnings_due(tickers, state, calendar, reports, today, have):
    """Le aziende di cui rileggere gli utili, nell'ordine dato: prima le trimestrali nuove, poi chi non li ha mai avuti.

    Una trimestrale è nuova se è passata (al più tardi ieri), è dopo l'ultima che
    abbiamo, e non l'abbiamo già cercata abbastanza: dopo `RETRY_DAYS` giorni dal
    suo annuncio si smette fino alla prossima. Le date vengono dal calendario
    di Alpha Vantage e dagli 8-K con item 2.02 (risultati) delle schede.
    """
    y = (today - timedelta(days=1)).isoformat()
    lo = (today - timedelta(days=REPORT_LOOKBACK)).isoformat()
    on = state.get("earnings_on") or {}
    refresh, never = [], []
    for t in tickers:
        if t not in have:
            never.append(t)
            continue
        last = have[t]
        seen = set(reports.get(t) or []) | set((calendar.get("seen") or {}).get(t) or [])
        new = [r for r in seen if lo <= r <= y and (not last or r > last)]
        if not new:
            continue
        r, fetched = max(new), on.get(t)
        if fetched == today.isoformat():
            continue                       # già chiesto oggi
        if fetched and fetched > (date.fromisoformat(r) + timedelta(days=RETRY_DAYS)).isoformat():
            continue                       # cercato per RETRY_DAYS giorni dopo l'annuncio: si aspetta la prossima
        refresh.append(t)
    return refresh + never


def update(folder, tickers, key, budget, today=None, reports=None, per_day=EARNINGS_PER_DAY, get=None):
    """Il giro giornaliero. -> (richieste usate, righe di riepilogo, fermato dal limite?).

    `tickers` è l'universo nell'ordine in cui serve di più (lo stesso delle
    trascrizioni); `reports` {ticker: [date delle trimestrali dagli 8-K]};
    `budget` quante richieste si possono usare in tutto oggi.
    """
    today = today or date.today()
    p = _paths(folder)
    state = _read(p["state"], {})
    used, log = 0, []
    tickers = [t.upper() for t in tickers]

    def ask(params):
        nonlocal used
        used += 1
        return request(params, key, get)

    stopped = False
    try:
        # 1. l'elenco dei titoli quotati: una richiesta alla settimana. Se va
        # storto si riprova al giro dopo; un elenco quasi vuoto non si salva,
        # altrimenti metà dell'universo diventerebbe "non quotato" sul sito.
        last = state.get("listing_on")
        if budget - used >= 1 and (not last or (today - date.fromisoformat(last)).days >= LISTING_DAYS):
            try:
                active = parse_listing(ask({"function": "LISTING_STATUS"}), tickers)
                if len(active) < LISTING_MIN_SHARE * len(tickers):
                    raise ValueError(f"l'elenco ha solo {len(active)} dei {len(tickers)} ticker: non lo uso")
                _write(p["listing"], {"on": today.isoformat(), "active": active, "delisted": {}})
                state["listing_on"] = today.isoformat()
                missing = [t for t in tickers if t not in active]
                log.append(f"TITOLI elenco aggiornato: {len(active)} su {len(tickers)} quotati"
                           + (f"; non quotati: {', '.join(missing)}" if missing else ""))
            except (ValueError, csv.Error) as e:
                log.append(f"ERR   elenco dei titoli: {str(e)[:200]} (si riprova al prossimo giro)")

        # 2. il calendario delle trimestrali: una richiesta al giorno
        cal = _read(p["calendar"], {})
        if budget - used >= 1 and state.get("calendar_on") != today.isoformat():
            try:
                nxt = parse_calendar(ask({"function": "EARNINGS_CALENDAR", "horizon": "3month"}), tickers)
                seen = cal.get("seen") or {}
                for t, rows in nxt.items():
                    seen[t] = sorted(set(seen.get(t) or []) | {r["date"] for r in rows})[-8:]
                cal = {"on": today.isoformat(), "next": nxt, "seen": seen}
                _write(p["calendar"], cal)
                state["calendar_on"] = today.isoformat()
                log.append(f"CALENDARIO {sum(len(v) for v in nxt.values())} trimestrali in arrivo "
                           f"per {len(nxt)} aziende nei prossimi tre mesi")
            except (ValueError, csv.Error) as e:
                log.append(f"ERR   calendario: {str(e)[:200]} (si riprova al prossimo giro)")

        # 3. gli utili: prima chi ha appena annunciato, poi chi non li ha ancora
        have = {}
        for t in tickers:
            e = _read(os.path.join(p["earnings"], f"{t}.json"), None)
            if e is not None:
                have[t] = (e.get("rows") or [{}])[-1].get("date") if e.get("rows") else None
        due = earnings_due(tickers, state, cal, reports or {}, today, have)
        on = state.setdefault("earnings_on", {})
        n = 0
        for t in due:
            if n >= per_day or used >= budget:
                break
            n += 1
            try:
                rows = parse_earnings(ask({"function": "EARNINGS", "symbol": t}))
            except Limit:
                raise
            except Exception as e:            # un'azienda che non va non ferma le altre
                log.append(f"ERR   utili di {t}: {str(e)[:150]}")
                on[t] = today.isoformat()
                continue
            on[t] = today.isoformat()
            _write(os.path.join(p["earnings"], f"{t}.json"), {"on": today.isoformat(), "rows": rows})
            lastq = rows[-1] if rows else None
            log.append(f"UTILI {t}: {len(rows)} trimestri"
                       + (f", ultimo annunciato il {lastq['date']}" + (f" (sorpresa {lastq['sp']:+.1f}%)"
                                                                      if lastq.get("sp") is not None else "")
                          if lastq else ", nessun dato"))
        left = len(due) - n
        if left > 0:
            log.append(f"UTILI ne restano {left} per i prossimi giorni")
    except Limit as e:
        log.append(f"STOP  Alpha Vantage: {e}")
        stopped = True
    except Exception as e:                    # qualunque altro guaio: lo si scrive e si va avanti
        log.append(f"ERR   Alpha Vantage (utili, calendario, titoli): {type(e).__name__}: {str(e)[:200]}")
    _write(p["state"], state)
    return used, log, stopped


# ------------------------------------------------------------------ per il giro del sito
def load(folder=DIR):
    """Quello che il giro orario usa: utili, prossime trimestrali, elenco dei titoli.

    Se la cartella non c'è (il workflow delle trascrizioni non è ancora
    passato) tutto è vuoto e il sito funziona come prima.
    """
    p = _paths(folder)
    earnings = {}
    if os.path.isdir(p["earnings"]):
        for name in sorted(os.listdir(p["earnings"])):
            if name.endswith(".json"):
                e = _read(os.path.join(p["earnings"], name), {})
                earnings[name[:-5]] = e.get("rows") or []
    cal = _read(p["calendar"], {})
    lst = _read(p["listing"], {})
    return {"earnings": earnings, "next": cal.get("next") or {}, "calendar_on": cal.get("on"),
            "listing": lst if lst.get("on") else None}


def next_report(av, ticker, today):
    """La prossima trimestrale del calendario, da oggi in poi, o None."""
    for r in (av.get("next") or {}).get(ticker) or []:
        if r["date"] >= today.isoformat():
            return r
    return None


def listing_problem(av, ticker):
    """Una frase se il ticker non risulta quotato nell'ultimo elenco, altrimenti None."""
    lst = av.get("listing")
    if not lst or ticker in (lst.get("active") or {}):
        return None
    gone = (lst.get("delisted") or {}).get(ticker)
    if gone:
        return (f"{ticker} non risulta più quotato: tolto dalla borsa il {gone.get('delisted') or '?'} "
                f"(elenco Alpha Vantage del {lst['on']}). Va sostituito in config.json.")
    return (f"{ticker} non compare fra i titoli quotati nell'elenco Alpha Vantage del {lst['on']}: "
            f"controlla se è stato tolto dalla borsa o ha cambiato ticker, e nel caso sostituiscilo "
            f"in config.json.")


def last_surprise(rows, day, max_days=SURPRISE_DAYS):
    """L'ultima trimestrale annunciata PRIMA di `day` (entro `max_days` giorni) con una sorpresa nota, o None.

    Prima in senso stretto: chi compra il giorno stesso dell'annuncio può averlo
    fatto prima che i numeri uscissero.
    """
    d = day[:10] if isinstance(day, str) else day.isoformat()
    lo = (date.fromisoformat(d) - timedelta(days=max_days)).isoformat()
    for r in reversed(rows or []):
        if r["date"] < d:
            if r["date"] < lo:
                return None
            return r if r.get("sp") is not None else None
    return None
