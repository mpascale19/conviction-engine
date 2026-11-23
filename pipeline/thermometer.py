"""Il termometro degli insider sul mercato.

Il resto del progetto guarda un'azienda alla volta. Qui si guardano tutte
insieme: in un dato periodo, quanti insider comprano azioni della propria
azienda e quanti ne vendono? È un'idea vecchia della finanza (Seyhun, 1988 e
1992; Lakonishok e Lee, "Are Insider Trades Informative?", 2001): gli insider
nel loro insieme comprano di più quando la borsa è scesa e sembra a buon
mercato, e in quegli studi la borsa nei mesi dopo è andata meglio della media.

La lettura:

- **la finestra è di tre mesi**: una trimestrale per azienda, così le finestre
  di acquisto che si aprono dopo i conti (molto più affollate a febbraio,
  maggio, agosto e novembre) pesano sempre allo stesso modo;
- **si contano le persone, non i dollari**: un insider (azienda, nome) che
  compra sul mercato almeno una volta nella finestra è un compratore; uno che
  vende fuori da un piano 10b5-1 è un venditore. I dollari li deciderebbero
  pochi amministratori molto ricchi;
- **la lettura è la quota di compratori**: compratori / (compratori +
  venditori), in percentuale. Le vendite sono sempre molte di più (stipendi
  pagati in azioni, tasse, diversificazione), quindi la quota è bassa: conta
  se è alta o bassa rispetto alla sua storia, non il numero in sé;
- **ogni azienda conta solo nei mesi in cui la sua storia è intera**: mentre
  la storia dei Form 4 si riempie a rate un'azienda entra nel termometro dal
  primo mese intero dopo il suo documento mancante più recente.

La prova: a ogni fine mese si legge il termometro e si guarda che cosa ha
fatto la borsa (lo SPY, l'S&P 500) nei tre e nei sei mesi dopo. Il rendimento
parte dal giorno 5 del mese successivo, perché il Form 4 arriva entro due
giorni lavorativi e a fine mese le ultime operazioni non si conoscono ancora.
Si mostrano tre cose: il rendimento medio nei terzi di mesi con la lettura più
bassa, media e più alta (gli stessi tre gruppi per i due orizzonti); la pendenza della retta (quanto rende in più la borsa
per ogni 10 punti in più di compratori) con un errore standard di Newey-West,
perché finestre di tre mesi e rendimenti di sei che si sovrappongono rendono i
mesi vicini dipendenti; la correlazione di rango di Spearman.

Dieci anni sono 120 mesi, ma con rendimenti a sei mesi le prove davvero
indipendenti sono una ventina: il risultato va letto con questa misura.
"""
import calendar
from datetime import date, timedelta

WINDOW = 3            # mesi della finestra mobile
MIN_PEOPLE = 30       # sotto questo numero di insider attivi nella finestra la lettura non si dà
HORIZONS = (3, 6)     # mesi dopo la lettura
START_DAY = 5         # il rendimento parte da questo giorno del mese dopo
MIN_MONTHS = 24       # sotto, la prova non si fa
COVER_MIN = 0.9       # nella prova entrano i mesi con almeno il 90% delle aziende coperte
MARKET = "SPY"        # la borsa: il riferimento del gruppo "Grandi capitalizzazioni USA"


def _ym(day):
    return int(day[:4]), int(day[5:7])


def _key(y, m):
    return f"{y:04d}-{m:02d}"


def _add(y, m, k):
    n = y * 12 + (m - 1) + k
    return n // 12, n % 12 + 1


def full_from(d):
    """Il primo mese ("AAAA-MM") in cui la storia dei Form 4 dell'azienda è intera, o None."""
    start = d.get("_tx_start")
    if not start:
        return None
    holes = (d.get("history") or {}).get("tx_holes_until")
    first = start[:10]
    if holes:
        nxt = (date.fromisoformat(holes[:10]) + timedelta(days=1)).isoformat()
        first = max(first, nxt)
    y, m = _ym(first)
    if first[8:10] != "01":
        y, m = _add(y, m, 1)
    return _key(y, m)


def series(results, today):
    """La lettura di ogni mese, dalla prima finestra coperta al mese in corso.

    -> [{m, b, s, r, co, part}]: il mese in cui la finestra finisce, i compratori,
    i venditori, la quota di compratori (in %, None se gli insider attivi sono
    meno di MIN_PEOPLE), le aziende coperte, e se il mese è ancora in corso.
    """
    now = _key(*_ym(today))
    buy, sell, cover, since = {}, {}, {}, {}
    for d in results:
        first = full_from(d)
        if not first or first > now:
            continue
        t = d["company"]["ticker"]
        since[t] = first
        y, m = _ym(first + "-01")
        while _key(y, m) <= now:
            cover[_key(y, m)] = cover.get(_key(y, m), 0) + 1
            y, m = _add(y, m, 1)
        for x in d.get("tx") or []:
            k = x["date"][:7]
            if k < first or k > now:
                continue
            if x["kind"] == "BUY":
                buy.setdefault(k, set()).add((t, x["who"]))
            elif x["kind"] == "SELL_D":
                sell.setdefault(k, set()).add((t, x["who"]))
    if not cover:
        return []
    months = sorted(cover)
    out = []
    for k in months:
        y, m = _ym(k + "-01")
        win = [_key(*_add(y, m, -i)) for i in range(WINDOW)]
        if any(w not in cover for w in win):
            continue                              # la finestra comincia prima della storia
        # entrano solo le aziende coperte per tutta la finestra
        b = {p for w in win for p in buy.get(w, ()) if since[p[0]] <= win[-1]}
        s = {p for w in win for p in sell.get(w, ()) if since[p[0]] <= win[-1]}
        n = len(b) + len(s)
        out.append({"m": k, "b": len(b), "s": len(s),
                    "r": round(len(b) / n * 100, 1) if n >= MIN_PEOPLE else None,
                    # le aziende coperte in tutta la finestra: quelle del suo primo mese
                    "co": cover[win[-1]], "part": k == now})
    return out


def _close_on_or_after(px, day):
    """La prima chiusura dal giorno `day` in poi: (giorno, chiusura) o None."""
    for p in px:
        if p["d"] >= day and p["c"]:
            return p["d"], p["c"]
    return None


def forward(px, month, months):
    """Il rendimento della borsa, in %, dal giorno START_DAY del mese dopo `month` a `months` mesi più in là.

    None se i prezzi non arrivano fin lì.
    """
    y, m = _add(*_ym(month + "-01"), 1)
    a = _close_on_or_after(px, date(y, m, START_DAY).isoformat())
    y2, m2 = _add(y, m, months)
    end_day = date(y2, m2, min(START_DAY, calendar.monthrange(y2, m2)[1])).isoformat()
    if not a or not px or px[-1]["d"] < end_day:
        return None
    b = _close_on_or_after(px, end_day)
    if not b:
        return None
    return (b[1] / a[1] - 1) * 100


def ols_nw(x, y, lags):
    """Retta y = a + b·x con l'errore standard di Newey-West della pendenza.

    -> (b, se) o None se i dati non bastano.
    """
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    if sxx == 0:
        return None
    b = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / sxx
    a = my - b * mx
    u = [(x[i] - mx) * (y[i] - a - b * x[i]) for i in range(n)]   # punteggio della pendenza
    s = sum(v * v for v in u)
    for L in range(1, min(lags, n - 1) + 1):
        w = 1 - L / (lags + 1)                                    # pesi di Bartlett
        s += 2 * w * sum(u[i] * u[i - L] for i in range(L, n))
    var = s / sxx ** 2 * n / (n - 2)
    return b, max(var, 0.0) ** 0.5


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1                         # i pari merito hanno il rango medio
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((v - mx) ** 2 for v in rx) * sum((v - my) ** 2 for v in ry)) ** 0.5
    return num / den if den else None


def bands(readings):
    """I tre terzi delle letture, dalla più bassa alla più alta: [(da, a)] o None.

    Si fanno una volta sola su tutte le letture della prova, così i tre gruppi
    sono gli stessi per i rendimenti a tre e a sei mesi.
    """
    v = sorted(readings)
    n = len(v)
    if n < 3:
        return None
    return [(v[i * n // 3], v[(i + 1) * n // 3 - 1]) for i in range(3)]


def by_band(pairs, cuts):
    """[(lettura, rendimento)] -> rendimento medio e quota positiva in ciascun terzo."""
    out = []
    for i, (lo, hi) in enumerate(cuts):
        top = cuts[i + 1][0] if i + 1 < len(cuts) else None
        ret = [v for r, v in pairs if r >= lo and (top is None or r < top)]
        out.append({"n": len(ret), "mean": round(sum(ret) / len(ret), 2) if ret else None,
                    "pos": round(sum(1 for v in ret if v > 0) / len(ret) * 100) if ret else None})
    return out


def test(rows, px):
    """La prova sulle letture chiuse e ben coperte. -> {from, to, months, bands, h: {orizzonte: {...}}}."""
    top = max((r["co"] for r in rows), default=0)
    ok = [r for r in rows if not r["part"] and r["r"] is not None and r["co"] >= COVER_MIN * top]
    cuts = bands([r["r"] for r in ok])
    out = {}
    for h in HORIZONS:
        pairs = [(r["r"], f) for r in ok for f in [forward(px, r["m"], h)] if f is not None]
        if len(pairs) < MIN_MONTHS or not cuts:
            out[str(h)] = {"n": len(pairs)}
            continue
        x, y = [p[0] for p in pairs], [p[1] for p in pairs]
        lags = h - 1 + WINDOW - 1
        fit = ols_nw(x, y, lags)
        rho = spearman(x, y)
        res = {"n": len(pairs), "all": round(sum(y) / len(y), 2),
               "pos": round(sum(1 for v in y if v > 0) / len(y) * 100),
               "terc": by_band(pairs, cuts), "rho": round(rho, 2) if rho is not None else None,
               "lags": lags}
        if fit:
            b, se = fit
            res.update(slope=round(b * 10, 2), se=round(se * 10, 2),      # per 10 punti di quota
                       t=round(b / se, 2) if se else None)
        out[str(h)] = res
    used = [r["m"] for r in ok]
    return {"from": used[0] if used else None, "to": used[-1] if used else None,
            "months": len(ok), "bands": [list(c) for c in cuts] if cuts else None, "h": out}


def market_prices(results, symbol=MARKET):
    """Le chiusure della borsa: il riferimento del gruppo che usa `symbol`."""
    for d in results:
        if d.get("benchmark") == symbol and d.get("_bench_long"):
            return d["_bench_long"]
    return []


def run(results, today=None):
    """-> il blocco "thermometer" dell'indice, o None se non c'è ancora niente da leggere.

    `today`: "AAAA-MM-GG", per le prove; di solito oggi.
    """
    today = today or date.today().isoformat()
    rows = series(results, today)
    if not rows:
        return None
    px = market_prices(results)
    closed = [r["r"] for r in rows if not r["part"] and r["r"] is not None]
    now = rows[-1]
    pct = None
    if now["r"] is not None and closed:
        pct = round(sum(1 for v in closed if v <= now["r"]) / len(closed) * 100)
    srt = sorted(closed)
    med = None
    if srt:
        k = len(srt) // 2
        med = srt[k] if len(srt) % 2 else (srt[k - 1] + srt[k]) / 2
    y, m = _ym(now["m"] + "-01")
    since = _key(*_add(y, m, -(WINDOW - 1))) + "-01"
    return {"window": WINDOW, "min_people": MIN_PEOPLE, "start_day": START_DAY,
            "market": MARKET, "cover_min": COVER_MIN, "companies": len(results),
            "series": rows,
            "now": {"m": now["m"], "r": now["r"], "b": now["b"], "s": now["s"], "co": now["co"],
                    "from": since, "to": today, "pct": pct,
                    "median": round(med, 1) if med is not None else None},
            "test": test(rows, px) if px else None}
