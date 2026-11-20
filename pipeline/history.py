"""Il registro delle previsioni: il track record del punteggio, in avanti.

Il backtest che c'è già mette alla prova due regole dentro la voce insider, non
il punteggio. Il punteggio non si può provare all'indietro: guidance e short
interest non esistono giorno per giorno nel passato, e ricostruirli vorrebbe
dire inventarli.

Quello che si può fare è l'altra cosa, che è anche la più onesta: segnare il
punteggio di oggi, aspettare, e poi misurare come è andata. Da qui in avanti
ogni seduta finisce una riga per azienda in `site/history.csv`, e quando sono
passati 90 giorni quella riga diventa un risultato — giusto o sbagliato che sia.

Il file è in sola aggiunta e sta dentro `site/`, quindi chiunque può scaricarlo
e rifare i conti. Non viene mai riscritto: una previsione registrata non si
corregge dopo aver visto come è andata.
"""
import csv
import os
from datetime import date

HORIZON_DAYS = 90          # giorni di calendario, non sedute: regge anche se
                           # il workflow salta qualche giro
COLS = ["date", "ticker", "score", "close", "bench", "bench_close"]

# Le fasce sono quelle del verdetto scritto: se un giorno cambiano lì,
# cambiano anche qui, e il registro resta confrontabile con quello che il
# sito dice a parole.
BANDS = [
    (65, 101, "convinzione rialzista", "bullish conviction"),
    (55, 65, "leggermente rialzista", "mildly bullish"),
    (46, 55, "nessuna convinzione", "no clear conviction"),
    (35, 46, "leggermente ribassista", "mildly bearish"),
    (0, 35, "convinzione ribassista", "bearish conviction"),
]


def band_of(score):
    for lo, hi, it, en in BANDS:
        if lo <= score < hi:
            return it
    return None


def load(path):
    """Le righe gia' registrate. Un file assente e' un registro vuoto."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"date": r["date"], "ticker": r["ticker"],
                            "score": int(r["score"]), "close": float(r["close"]),
                            "bench": r["bench"], "bench_close": float(r["bench_close"])})
            except (KeyError, ValueError, TypeError):
                continue          # una riga rotta non deve far cadere il giro
    return out


def add(path, rows, known=None):
    """Aggiunge le righe per cui non c'e' gia' quella (data, ticker).

    Solo aggiunta, mai riscrittura: il registro e' la prova, e una prova che
    si puo' riscrivere non e' una prova.
    """
    seen = {(r["date"], r["ticker"]) for r in (load(path) if known is None else known)}
    new = [r for r in rows if (r["date"], r["ticker"]) not in seen]
    if not new:
        return 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fresh = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if fresh:
            w.writeheader()
        for r in new:
            w.writerow({k: r[k] for k in COLS})
    return len(new)


def snapshot(results, bench_closes, today=None):
    """Una riga per azienda, datata al giorno dell'ultima chiusura.

    La data e' quella del prezzo, non quella del giro: lega il punteggio a un
    giorno di borsa, e i giri successivi dello stesso giorno non aggiungono
    doppioni.
    """
    rows = []
    for d in results:
        last = (d.get("price") or {}).get("last")
        s = (d.get("score") or {}).get("value")
        if not last or s is None or not last.get("c"):
            continue
        bench = (d.get("benchmark") or "")
        bc = bench_closes.get((bench, last["d"]))
        if not bc:
            continue              # senza il riferimento non si puo' misurare
        # Un prezzo a zero non e' un prezzo: entrerebbe nel registro per poi
        # sparire in fase di misura, e i tre conti del sito smetterebbero di
        # tornare (registrate = misurate + in attesa).
        rows.append({"date": last["d"], "ticker": d["company"]["ticker"],
                     "score": int(s), "close": round(float(last["c"]), 4),
                     "bench": bench, "bench_close": round(float(bc), 4)})
    return rows


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def evaluate(rows, horizon=HORIZON_DAYS):
    """Chiude le previsioni che hanno compiuto l'orizzonte.

    Per ogni riga cerca, sullo stesso ticker, la prima riga successiva distante
    almeno `horizon` giorni: quella da' i due prezzi di arrivo. Il rendimento e'
    sempre al netto del riferimento di settore, come nel resto del progetto.
    """
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    for v in by.values():
        v.sort(key=lambda r: r["date"])

    done, pending = [], 0
    for t, v in by.items():
        for i, r in enumerate(v):
            later = next((x for x in v[i + 1:] if _days(r["date"], x["date"]) >= horizon), None)
            if later is None:
                pending += 1
                continue
            if not r["close"] or not r["bench_close"]:
                continue
            mine = later["close"] / r["close"] - 1
            bench = later["bench_close"] / r["bench_close"] - 1
            done.append({"ticker": t, "from": r["date"], "to": later["date"],
                         "score": r["score"], "band": band_of(r["score"]),
                         "ret": round((mine - bench) * 100, 2)})
    return done, pending


def stats(rows, horizon=HORIZON_DAYS):
    """Il riassunto per il sito: una riga per fascia di punteggio."""
    done, pending = evaluate(rows, horizon)
    first = min((r["date"] for r in rows), default=None)
    out = {"horizon": horizon, "started": first,
           "observations": len(rows), "pending": pending, "done": len(done),
           "days": len({r["date"] for r in rows}), "bands": []}
    if not done:
        return out
    for lo, hi, it, en in BANDS:
        xs = [r["ret"] for r in done if lo <= r["score"] < hi]
        out["bands"].append({
            "lo": lo, "hi": hi, "it": it, "en": en, "n": len(xs),
            "pos": sum(1 for x in xs if x > 0) if xs else 0,
            "med": round(_median(xs), 2) if xs else None,
            "avg": round(sum(xs) / len(xs), 2) if xs else None,
        })
    allr = [r["ret"] for r in done]
    out["all"] = {"n": len(allr), "pos": sum(1 for x in allr if x > 0),
                  "med": round(_median(allr), 2),
                  "avg": round(sum(allr) / len(allr), 2)}
    return out
