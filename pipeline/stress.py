"""Quanto dipende il punteggio dai pesi che ho scelto io.

I pesi della formula (35 insider, 20 guidance, 15 mercato, 10 settore, 10 fatti
rilevanti, 10 prezzo) sono numeri scritti a mano. Nessuno li ha stimati, e
nessuno li ha mai messi in discussione: finché resta così, la classifica
potrebbe essere soltanto il riflesso di quella scelta arbitraria.

Qui si prova il contrario. Il punteggio viene ricalcolato con pesi diversi e si
guarda che cosa resta in piedi:

- **pesi uguali**: come verrebbe la classifica se non avessi nessuna opinione;
- **una voce tolta alla volta**: quanto pesa davvero ogni ingresso;
- **pesi tirati a caso**, ognuno libero di valere da metà al doppio di quello
  scritto: il quadro d'insieme, ripetuto molte volte.

Da qui escono due cose. Per il sito intero: quanto la classifica si muove.
Per ogni azienda: dentro che fascia sta il suo punteggio quando i pesi
cambiano, e quante volte il verdetto scritto resta quello.

Il seme del caso è fisso. Non è un dettaglio: se le estrazioni cambiassero a
ogni giro, ogni scheda risulterebbe diversa anche quando nei dati non è
cambiato niente, e il sito si riscriverebbe tutto ogni ora per rumore.
"""
import random

SEED = 20261120          # fisso: stessi dati, stesso risultato
DRAWS = 600
LO, HI = 0.5, 2.0        # ogni peso libero di valere da metà al doppio
TOP = 10                 # quante prime posizioni si confrontano


def _rank(scores):
    order = sorted(scores, key=lambda t: (-scores[t], t))
    return {t: i for i, t in enumerate(order)}


def spearman(a, b):
    """Correlazione di rango fra due classifiche sugli stessi ticker.

    1 = identiche, 0 = nessun rapporto. Guarda l'ordine, non i punteggi: è la
    domanda giusta, perché il sito si legge come una classifica.
    """
    ra, rb = _rank(a), _rank(b)
    n = len(ra)
    if n < 3:
        return None
    d2 = sum((ra[t] - rb[t]) ** 2 for t in ra)
    return round(1 - 6 * d2 / (n * (n * n - 1)), 3)


def _top(scores, k=TOP):
    return set(sorted(scores, key=lambda t: (-scores[t], t))[:k])


def _pct(xs, p):
    """Percentile su una lista già ordinata, senza interpolazione."""
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))
    return xs[i]


def _shift(a, b):
    common = [t for t in a if t in b and a[t] is not None and b[t] is not None]
    if not common:
        return None
    return round(sum(abs(a[t] - b[t]) for t in common) / len(common), 1)


def _score_all(subs_by_ticker, weights, combine):
    out = {}
    for t, subs in subs_by_ticker.items():
        use = {k: v for k, v in subs.items() if k in weights}
        if not use:
            continue
        v = combine(use, weights)[0]
        if v is not None:
            out[t] = v
    return out


def run(subs_by_ticker, weights, combine, verdict, draws=DRAWS):
    """L'analisi completa. `combine` e `verdict` arrivano da score.py: la prova
    deve usare la stessa funzione del sito, non una sua copia riscritta qui.
    """
    base = _score_all(subs_by_ticker, weights, combine)
    if len(base) < 5:
        return None
    keys = list(weights)
    top0 = _top(base)

    # 1. pesi uguali: la classifica senza nessuna opinione
    flat = {k: 100.0 / len(keys) for k in keys}
    s_eq = _score_all(subs_by_ticker, flat, combine)
    equal = {"rho": spearman(base, s_eq), "shift": _shift(base, s_eq),
             "top": len(top0 & _top(s_eq))}

    # 2. una voce tolta alla volta, guardando solo chi quella voce ce l'ha
    drop = []
    for k in keys:
        have = {t: s for t, s in subs_by_ticker.items()
                if s.get(k, {}).get("v") is not None and t in base}
        if len(have) < 5:
            drop.append({"id": k, "n": len(have), "rho": None, "shift": None})
            continue
        w2 = {x: weights[x] for x in keys if x != k}
        s2 = _score_all({t: {a: b for a, b in s.items() if a != k}
                         for t, s in have.items()}, w2, combine)
        b2 = {t: base[t] for t in s2}
        drop.append({"id": k, "n": len(have), "rho": spearman(b2, s2),
                     "shift": _shift(b2, s2)})
    drop.sort(key=lambda d: (d["rho"] is None, d["rho"]))

    # 3. pesi a caso, molte volte
    rnd = random.Random(SEED)
    rhos, tops = [], []
    per = {t: [] for t in base}
    for _ in range(draws):
        w = {k: weights[k] * rnd.uniform(LO, HI) for k in keys}
        tot = sum(w.values())
        w = {k: v * 100.0 / tot for k, v in w.items()}
        s = _score_all(subs_by_ticker, w, combine)
        rhos.append(spearman(base, s))
        tops.append(len(top0 & _top(s)))
        for t in per:
            if t in s:
                per[t].append(s[t])
    rhos = sorted(x for x in rhos if x is not None)
    tops.sort()

    out_per, widths, kept = {}, [], []
    for t, vs in per.items():
        if not vs:
            continue
        vs.sort()
        lo, hi = _pct(vs, 5), _pct(vs, 95)
        want = verdict(base[t])
        want = want["it"] if isinstance(want, dict) else want
        k = sum(1 for v in vs if (lambda d: d["it"] if isinstance(d, dict) else d)(verdict(v)) == want)
        k = round(k / len(vs) * 100)
        out_per[t] = {"lo": lo, "hi": hi, "kept": k}
        widths.append(hi - lo)
        kept.append(k)

    widths.sort()
    return {
        "draws": draws, "lo": LO, "hi": HI, "top": TOP,
        "equal": equal, "drop": drop,
        "rho": {"med": _pct(rhos, 50), "p5": _pct(rhos, 5), "min": rhos[0] if rhos else None},
        "top_overlap": {"med": _pct(tops, 50), "min": tops[0] if tops else None},
        "width_med": _pct(widths, 50),
        "kept_avg": round(sum(kept) / len(kept)) if kept else None,
        "per": out_per,
    }
