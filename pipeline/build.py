"""Orchestratore: raccoglie i dati di tutto l'universo, applica le regole,
calcola i punteggi e scrive i file che il sito legge.

    python -m pipeline.build                  # tutti i ticker di config.json
    python -m pipeline.build --ticker AEHR    # uno solo
    python -m pipeline.build --offline        # ricalcola dalla cache, senza rete

Scrive:
    site/index.json          l'elenco con punteggio e riassunto di ogni azienda
    site/data/<TICKER>.json  il dettaglio di una singola azienda
    site/feed.xml            gli acquisti degli insider come feed RSS (feed-en.xml in inglese)
    site/radar.json          gli acquisti degli insider di tutto il mercato (pipeline/mercato.py)
"""
import argparse
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

from . import (alphavantage, attivisti, bonus, eventstudy, feed, finra, fondi, habits, history, market,
               mercato, predict, rules, score, stress, thermometer)
from .rules import L
from .extract import extract_all

CACHE = "cache"
OUTDIR = "site"
EVENT_DAYS = 180        # finestra dei fatti rilevanti 8-K: due trimestri
EVENT_HIST_DAYS = 3670  # quanto storico di 8-K tenere per la prova all'indietro: dieci anni
CARD_TX_MONTHS = 24     # di quanti mesi la scheda conta le operazioni
CARD_TX_DAYS = 120      # ... e di quanti giorni le porta per intero (il sito ne legge 90)
CARD_TX_LAST = 25       # più le ultime 25, che il sito mostra in tabella
NEW_FILINGS_PER_RUN = 2000   # Form 4 vecchi per giro, se config.json non dice altro
HISTORY_MINUTES = 35    # dopo quanti minuti di giro la storia vecchia aspetta il giro dopo
PRICE_COPY_DAYS = 5     # quanto può essere vecchia la copia dei prezzi se Yahoo non risponde
AV_DIR = alphavantage.DIR   # utili, calendario e titoli: li scarica il workflow delle trascrizioni
CARD_EARN_Q = 12        # quanti trimestri di utili contro attese porta la scheda
SITE_URL = "https://mpascale19.github.io/conviction-engine/"   # se config.json non dice altro
MK_DIR = mercato.DIR    # tutto il mercato e i grandi fondi: li scarica il workflow mercato.yml
CARD_INST_Q = 12        # quanti trimestri di 13F porta la scheda


def load_config(path="config.json"):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # compatibilita' con la vecchia forma a lista unica
    if "universi" not in cfg:
        cfg["universi"] = [{"nome": cfg.get("universo", "Universo"),
                            "benchmark": cfg.get("benchmark", "SPY"),
                            "tickers": cfg.get("tickers", [])}]
    return cfg


def group_name(g):
    """Il nome dell'universo nelle due lingue (nome_en è opzionale)."""
    return L(g["nome"], g.get("nome_en") or g["nome"])


# ----------------------------------------------------------------- raccolta
def prices_or_copy(symbol, max_age=PRICE_COPY_DAYS):
    """(righe, fonte) di oggi; se nessuna fonte risponde, la copia dell'ultimo giro riuscito.

    Duecento titoli vogliono duecento richieste a Yahoo ogni ora, e prima o poi
    Yahoo ne rifiuta qualcuna. Una chiusura di ieri è meglio di un'azienda
    senza punteggio, purché sia davvero di ieri: oltre `max_age` giorni la
    copia non vale più e l'errore resta.
    """
    from . import sources
    path = os.path.join(CACHE, "prices", f"{symbol}.json")
    try:
        px, src = sources.prices(symbol)
    except Exception as e:
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
            age = (date.today() - date.fromisoformat(old["px"][-1]["d"])).days
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            raise e
        if age > max_age:
            raise
        print(f"  ! prezzi: {e} -> uso la copia del giro precedente (ultima seduta {old['px'][-1]['d']})")
        # "(cache)" si legge nelle due lingue: la scheda mostra la fonte così com'è
        return old["px"], f"{old['src']} (cache)"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"px": px, "src": src}, f)
    return px, src


def gather(cfg, ticker, bench, offline=False, budget=None):
    """Prezzi e Form 4 di un ticker. Non solleva: registra gli errori.

    `budget`: quanti Form 4 nuovi si possono ancora scaricare in questo giro,
    condiviso fra tutte le aziende (vedi sources.insider_transactions).
    """
    raw_path = os.path.join(CACHE, f"raw_{ticker}.json")
    if offline:
        with open(raw_path, encoding="utf-8") as f:
            return json.load(f)

    from . import sources
    errors, failed, px, tx, src = [], [], [], [], None
    name, cik, shares, sh_date = ticker, "", None, None
    tx_status = {"complete": False, "missing": None}

    try:
        px, src = prices_or_copy(ticker)
        print(f"  prezzi: {len(px)} sedute da {src}, ultima {px[-1]['d']}")
    except Exception as e:
        errors.append(f"Prezzi di {ticker}: {e}")
        failed.append("prices")
        print(f"  ! prezzi: {e}")

    try:
        fallback = (cfg.get("cik") or {}).get(ticker)
        cik, name, how = sources.cik_for(ticker, fallback)
        print(f"  CIK {cik} ({name}) via {how}")
        # i CIK precedenti dell'azienda, se ha cambiato sede o capogruppo:
        # la sua storia sta sotto di loro (vedi sources.PREDECESSORS)
        olds = (cfg.get("cik_predecessors") or {}).get(ticker) or []
        if olds:
            sources.PREDECESSORS[str(cik).zfill(10)] = [str(c).zfill(10) for c in olds]
            print(f"  anche i depositi dei CIK precedenti: {', '.join(olds)}")
        tx = sources.insider_transactions(cik, cfg["months_of_history"],
                                          limit=cfg.get("max_filings_per_ticker"),
                                          # v2: i documenti letti prima del controllo
                                          # sull'emittente vanno riletti tutti, una volta
                                          cache_dir=os.path.join(CACHE, "form4-v2"),
                                          budget=budget, status=tx_status)
        print(f"  Form 4: {len(tx)} operazioni"
              + ("" if tx_status["complete"] else
                 f" (storico in riempimento: {tx_status['missing']} documenti al prossimo giro)"))
    except Exception as e:
        errors.append(f"Form 4 di {ticker}: {e}")
        failed.append("tx")
        print(f"  ! Form 4: {e}")

    events = events_hist = events_from = acts = None
    since_hist = (date.today() - timedelta(days=EVENT_HIST_DAYS)).isoformat()
    if cik:                               # azioni in circolazione: dalla copertina dei 10-Q/10-K
        shares, sh_date = sources.shares_outstanding(cik)
        if shares:
            print(f"  azioni in circolazione: {shares/1e6:.0f} milioni (al {sh_date})")
        try:                              # fatti rilevanti: stesso JSON, nessuna richiesta in più
            since = (date.today() - timedelta(days=EVENT_DAYS)).isoformat()
            # due anni, per poter rifare la prova all'indietro (pipeline/predict.py);
            # il punteggio di oggi usa solo gli ultimi EVENT_DAYS giorni
            events_hist = sources.recent_8k(cik, since_hist)
            events = [e for e in events_hist if e["date"] >= since]
            # Da quando l'elenco dei depositi è completo. filings_table va a
            # cercare negli archivi quando il file principale non basta; se un
            # archivio non risponde, prima di questa data "nessun 8-K" non vuol
            # dire niente e la prova all'indietro non la usa.
            _, events_from = sources.filings_table(cik, since_hist)
            print(f"  8-K negli ultimi {EVENT_DAYS} giorni: {len(events)} "
                  f"(storico dal {events_from}: {len(events_hist)})")
        except Exception as e:
            errors.append(f"8-K di {ticker}: {e}")
            print(f"  ! 8-K: {e}")
        try:                              # Schedule 13D: stesso JSON, più l'intestazione dei nuovi
            cols, _ = sources.filings_table(cik, since_hist)
            b13 = (budget if budget is not None else {}).setdefault("13d", {"left": attivisti.NEW_PER_RUN})
            acts = attivisti.collect(cols, cik, since_hist, os.path.join(CACHE, "13d"), b13)
            if acts:
                print(f"  13D: {sum(1 for a in acts if a['first'])} depositi iniziali, "
                      f"{sum(1 for a in acts if not a['first'])} correzioni")
        except Exception as e:
            acts = None
            print(f"  ! 13D: {e}")

    raw = {"ticker": ticker, "name": name, "cik": cik, "prices": px, "bench": bench,
           "tx": tx, "errors": errors, "failed": failed, "price_source": src,
           "shares": shares, "shares_date": sh_date, "events": events,
           "events_hist": events_hist, "events_from": events_from,
           "tx_status": tx_status, "activists": acts,
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    os.makedirs(CACHE, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    return raw


# ----------------------------------------------------------------- calcolo
def build_one(cfg, raw, extracted=None):
    px = raw.get("prices") or []
    bench = raw.get("bench") or []
    # scorpori noti che la chiusura di Yahoo non corregge (rules.neutralize)
    px, adjusted = rules.neutralize(px, bench, (cfg.get("corporate_actions") or {}).get(
        (raw.get("ticker") or "").upper()) or [])
    flagged = [j for j in rules.jumps(px, bench)
               if not any(abs((date.fromisoformat(j["d"]) - date.fromisoformat(a["d"])).days) <= 7
                          for a in adjusted)]
    R, W = cfg["rules"], cfg["weights"]
    failed = raw.get("failed") or []
    today = date.fromisoformat(px[-1]["d"]) if px else date.today()
    # La storia è di `months_of_history` mesi per data dell'operazione, non solo
    # per data di deposito: un Form 4 depositato in ritardo può dichiarare
    # un'operazione di anni prima (Teradyne ne ha una del 2014). Fuori dalla
    # finestra non ci sono prezzi per misurarla, e "da quando parte la storia"
    # direbbe una data falsa.
    oldest = (today - timedelta(days=31 * int(cfg.get("months_of_history") or 24))).isoformat()
    # Alcuni Form 4 scrivono la data con il fuso orario attaccato
    # ("2026-05-12-05:00", PTC): è una data XML valida, ma non una data ISO, e
    # il 23/11/2026 ha fatto cadere la prova su abituali e opportunisti. Da qui
    # in poi ogni data di operazione è AAAA-MM-GG.
    for t in raw.get("tx") or []:
        if isinstance(t.get("date"), str) and len(t["date"]) > 10:
            t["date"] = t["date"][:10]
    tx = rules.enrich(sorted([t for t in (raw.get("tx") or []) if t["date"] >= oldest],
                             key=lambda t: t["date"]))
    # Abituali o opportunisti (pipeline/habits.py): l'etichetta va solo se la
    # storia è completa, altrimenti un'abitudine sembrerebbe mancare.
    complete = bool((raw.get("tx_status") or {}).get("complete", True))
    for i, h in habits.classify(tx, oldest if complete else None).items():
        tx[i]["habit"] = h

    # Il cluster conta solo se è recente: tre acquisti di un anno fa non
    # dicono niente sul presente, e il resto del punteggio guarda 90 giorni.
    start90 = (today - timedelta(days=90)).isoformat()
    recent = [t for t in tx if t["date"] >= start90]
    cluster = rules.find_cluster(recent, R["cluster_days"], R["cluster_min_people"])
    tracks = rules.track_records(tx, px, bench, R["track_record_horizon_days"],
                                 R["track_record_prior"],
                                 window_start=cluster[0]["date"] if cluster else None)

    subs = {}
    if "tx" in failed:
        subs["insider"] = {"v": None, "coverage": "Assente",
                           "why": [L("I Form 4 non sono stati scaricati: vedi gli errori.",
                                     "The Form 4 filings were not downloaded: see the errors.")]}
    else:
        v, why, cov = score.insider_score(tx, cluster, tracks, cfg, today)
        subs["insider"] = {"v": v, "why": why, "coverage": cov}
    if px and "prices" not in failed:
        v, why, cov = score.price_score(px, today)
    else:
        v, why, cov = None, [L("Nessun prezzo scaricato.",
                               "No prices downloaded.")], "Assente"
    subs["price"] = {"v": v, "why": why, "coverage": cov}

    for k, note in [
        ("guid", L("Serve l'export delle trascrizioni delle call.",
                   "Needs the export of the earnings-call transcripts.")),
        ("market", L("Lo short interest di FINRA per questo titolo non è ancora arrivato.",
                     "FINRA short interest for this stock has not arrived yet.")),
        ("sector", L("In attesa del confronto con il resto dell'universo.",
                     "Waiting for the comparison with the rest of the universe.")),
        ("events", L("I depositi SEC non sono stati raggiunti.",
                     "SEC filings could not be reached.")),
    ]:
        subs[k] = {"v": None, "why": [note], "coverage": "Assente"}

    if raw.get("events") is not None:
        v, why, cov = score.events_score(raw["events"], EVENT_DAYS)
        subs["events"] = {"v": v, "why": why, "coverage": cov}

    buys_90d = any(t["kind"] == "BUY" for t in recent)

    # Utili contro attese (Alpha Vantage): l'ultima trimestrale prima del primo
    # acquisto degli ultimi 90 giorni. Il bonus che ne viene lo stima bonus.py.
    earn = raw.get("earnings") or []
    first_buy = min((t["date"] for t in recent if t["kind"] == "BUY"), default=None)
    beat = alphavantage.last_surprise(earn, first_buy) if first_buy else None
    beat = {"date": beat["date"], "sp": beat["sp"]} if beat else None
    if beat and subs["insider"].get("v") is not None:
        v, why, cov = score.insider_score(tx, cluster, tracks, cfg, today, beat=beat)
        subs["insider"] = {"v": v, "why": why, "coverage": cov}
    nxt = next((r for r in (raw.get("calendar") or []) if r["date"] >= today.isoformat()), None)

    # Lo short interest viene dai file pubblici di FINRA (pipeline/finra.py).
    # L'export manuale resta come riserva, finché FINRA non ha almeno otto
    # rilevazioni di quel titolo: è lo stesso dato, ma tutte le aziende devono
    # essere misurate con la stessa fonte appena si può.
    si, si_src = list(raw.get("si") or []), "finra"
    if len(si) < 8:
        manual = market.load_short_interest(raw["ticker"])
        if len(manual) > len(si):
            si, si_src = manual, "manual"
    si_pct = None
    if si:
        v, why, cov = score.market_score(si, today, raw.get("shares"))
        subs["market"] = {"v": v, "why": why, "coverage": cov}
        if raw.get("shares"):
            si_pct = round(si[-1]["si"] / raw["shares"] * 100, 3)

    if extracted:
        tones = [e.get("tone") for e in extracted if isinstance(e.get("tone"), (int, float))]
        if tones:
            last = tones[-4:]
            avg = round(sum(last) / len(last))
            why = [L(f"Tono medio delle ultime {len(last)} call estratte: {avg}.",
                     f"Average tone of the last {len(last)} extracted calls: {avg}.")]
            v = avg

            # Direzione della guidance nell'ultima call: quante voci alzate e
            # quante ridotte. È l'azienda che parla di se stessa, non un analista.
            ultima = extracted[-1]
            dirs = [g.get("direction") for g in (ultima.get("guidance") or [])]
            up, dn = dirs.count("alzata"), dirs.count("ridotta")
            if up or dn:
                v = max(0, min(100, v + max(-10, min(10, (up - dn) * 2.5))))
                q = ultima.get("quarter", "")
                why.append(L(f"Nell'ultima call ({q}) l'azienda ha alzato {up} voci di guidance "
                             f"e ne ha ridotte {dn}.",
                             f"In the latest call ({q}) the company raised {up} guidance items "
                             f"and lowered {dn}."))
                if dn > up and buys_90d:
                    why.append(L("Gli insider hanno comprato mentre l'azienda tagliava la guidance: "
                                 "è una divergenza fra quello che dice e quello che fa.",
                                 "Insiders bought while the company was cutting guidance: "
                                 "a divergence between what it says and what it does."))
                elif up > dn and not buys_90d:
                    why.append(L("L'azienda alza la guidance ma nessun insider compra.",
                                 "The company raises guidance but no insider is buying."))
            subs["guid"] = {"v": round(v), "coverage": "Media", "why": why}

    # riassunto degli insider degli ultimi 90 giorni: serve al punteggio di settore
    win = recent
    buys = [t for t in win if t["kind"] == "BUY"]
    sells = [t for t in win if t["kind"] == "SELL_D"]
    summary = {
        "ticker": raw["ticker"],
        "buyers": len({t["who"] for t in buys}),
        "sellers": len({t["who"] for t in sells}),
        "buy_musd": round(sum(t["value"] for t in buys) / 1e6, 3),
        "sell_musd": round(sum(t["value"] for t in sells) / 1e6, 3),
        "net_musd": round((sum(t["value"] for t in buys) - sum(t["value"] for t in sells)) / 1e6, 3),
        "cluster": bool(cluster),
        "si_pct": si_pct,                 # per il confronto con i simili
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "errors": list(raw.get("errors") or []),
        "price_source": raw.get("price_source"),
        "company": {"ticker": raw["ticker"], "name": raw["name"], "cik": raw["cik"]},
        "price": {"last": px[-1] if px else None,
                  "series": [[p["d"], p["c"]] for p in px[-504:]]},
        "counts": {"tx": len(tx), "prices": len(px), "si": len(si),
                   "events": len(raw["events"]) if raw.get("events") is not None else None},
        "insider_90d": summary,
        "calls": [{k: e[k] for k in ("quarter", "call_date", "tone", "tone_why",
                                     "guidance", "qa", "language_changes") if k in e}
                  for e in (extracted or [])],
        "si_series": [[r["date"], round(r["si"]), r["dtc"]] for r in si[-48:]],
        "si_source": si_src if si else None,
        # cinque anni di short interest per la prova all'indietro: restano in
        # memoria, nella scheda vanno gli ultimi due anni
        "_si_long": [[r["date"], round(r["si"]), r["dtc"]] for r in si],
        # gli 8-K di due anni, per la prova all'indietro; e da quando il file
        # della SEC li copre davvero
        "events_hist": ([[e["date"], e["items"]] for e in raw["events_hist"]]
                        if raw.get("events_hist") is not None else None),
        "events_from": raw.get("events_from"),
        # quanto è lungo lo storico dei Form 4, e se ha finito di riempirsi
        "history": {"tx_from": tx[0]["date"] if tx else None,
                    "tx_complete": bool((raw.get("tx_status") or {}).get("complete")),
                    "tx_missing": (raw.get("tx_status") or {}).get("missing"),
                    # se la storia ha buchi, il giorno del documento mancante più recente:
                    # dopo quel giorno è intera (vedi sources.insider_transactions)
                    "tx_holes_until": (raw.get("tx_status") or {}).get("holes_until")},
        # dieci anni di chiusure per la prova all'indietro: restano in memoria,
        # nella scheda va solo la serie corta (vedi card_view)
        "_px_long": [[p["d"], p["c"]] for p in px],
        "_tx_start": oldest,               # da quando la storia dei Form 4 copre tutto
        # i salti di prezzo: quelli corretti perché scorpori noti, e quelli da controllare
        "_adjusted": adjusted,
        "_jumps": flagged,
        "si_pct": si_pct,
        "shares": raw.get("shares"),
        # utili contro attese degli ultimi tre anni e la prossima trimestrale
        # (Alpha Vantage); tutti i trimestri restano in memoria per le prove
        "earnings": [{"q": e["fiscal_end"], "d": e["date"], "eps": e["eps"], "est": e["est"],
                      "sp": e["sp"]} for e in earn[-CARD_EARN_Q:]] or None,
        "next_report": nxt,
        "_earn_long": earn,
        # chi ha superato il 5% per contare (Schedule 13D, pipeline/attivisti.py)
        "activists": attivisti.summary(raw.get("activists") or [], today.isoformat())
                     if raw.get("activists") is not None else None,
        "_13d": raw.get("activists") or [],
        "tx": tx,
        "cluster": [t["date"] + " " + t["who"] for t in cluster],
        "tracks": tracks,
        "_subs": subs,
        # quello che serve a ricalcolare la voce insider dopo che i bonus sono
        # stati stimati su tutte le aziende; non finisce nella scheda
        "_ins": {"cluster": cluster, "today": today.isoformat(), "beat": beat},
    }


def finish(cfg, data, universe):
    """Secondo passaggio: il settore si calcola solo contro i simili.

    `universe` sono i riassunti delle aziende dello stesso gruppo: confrontare
    un produttore di chip con una catena di supermercati non direbbe niente.
    """
    subs = data.pop("_subs")
    data.pop("_ins", None)
    me = data["insider_90d"]
    if data["company"]["cik"]:
        v, why, cov = score.sector_score(me, universe, cfg)
        subs["sector"] = {"v": v, "why": why, "coverage": cov}
    if "market" in subs:
        subs["market"] = score.market_peers(
            subs["market"], me.get("si_pct"),
            [u.get("si_pct") for u in universe if u["ticker"] != me["ticker"]])
    total, conf, missing, covered = score.combine(subs, cfg["weights"])
    data["score"] = {
        "value": total,
        "verdict": (score.verdict(total) if total is not None
                    else L("Dati insufficienti", "Not enough data")),
        "confidence": conf, "covered_pct": covered, "missing": missing,
        "subs": [{"id": k, "name": score.NAMES[k], "w": cfg["weights"][k], **subs[k]}
                 for k in cfg["weights"]],
    }
    px_n, tx_n = data["counts"]["prices"], data["counts"]["tx"]
    mesi, last_d = cfg["months_of_history"], (data["price"]["last"] or {}).get("d", "-")
    nu = len(universe)
    data["sources"] = [
        {"n": 1, "t": L("Form 4", "Form 4"),
         "m": L(f"SEC EDGAR, CIK {data['company']['cik'] or '-'}",
                f"SEC EDGAR, CIK {data['company']['cik'] or '-'}"),
         "x": L(f"{tx_n} operazioni negli ultimi {mesi} mesi.",
                f"{tx_n} transactions over the last {mesi} months.")},
        {"n": 2, "t": L("Prezzi di chiusura", "Closing prices"),
         "m": L(data.get("price_source") or "-", data.get("price_source") or "-"),
         "x": L(f"{px_n} sedute, ultima {last_d}",
                f"{px_n} trading days, last one {last_d}")},
        {"n": 3, "t": L("Short interest", "Short interest"),
         "m": (L("FINRA, file pubblici", "FINRA, public files") if data.get("si_source") == "finra"
               else L("Bloomberg, export manuale", "Bloomberg, manual export")),
         "x": L(f"{data['counts'].get('si', 0)} rilevazioni bimensili.",
                f"{data['counts'].get('si', 0)} twice-monthly readings.")}
        if data["counts"].get("si") else
        {"n": 3, "t": L("Short interest", "Short interest"),
         "m": L("non caricato", "not loaded"),
         "x": L("FINRA non ha ancora abbastanza rilevazioni di questo titolo.",
                "FINRA does not have enough readings of this stock yet.")},
        {"n": 4, "t": L("Universo di confronto", "Comparison universe"),
         "m": L(f"{nu} aziende", f"{nu} companies"),
         "x": data.get("group") or L("", "")},
        {"n": 5, "t": L("Fatti rilevanti (8-K)", "Material events (8-K)"),
         "m": L("SEC EDGAR", "SEC EDGAR"),
         "x": L(f"{data['counts'].get('events')} moduli negli ultimi {EVENT_DAYS} giorni.",
                f"{data['counts'].get('events')} filings in the last {EVENT_DAYS} days.")}
        if data["counts"].get("events") is not None else
        {"n": 5, "t": L("Fatti rilevanti (8-K)", "Material events (8-K)"),
         "m": L("non raggiunti", "not reached"),
         "x": L("I depositi SEC non hanno risposto in questo giro.",
                "The SEC filings did not answer on this run.")},
    ]
    if data.get("earnings"):
        e = data["earnings"]
        data["sources"].append(
            {"n": 6, "t": L("Utili contro attese", "Earnings against estimates"),
             "m": L("Alpha Vantage", "Alpha Vantage"),
             "x": L(f"{len(e)} trimestri, l'ultimo annunciato il {e[-1]['d']}.",
                    f"{len(e)} quarters, the latest reported on {e[-1]['d']}.")})
    return data


# ----------------------------------------------------------------- scrittura
def _stats(items):
    """Riassume un gruppo di acquisti.

    `ep` conta gli episodi, non le firme: cinque dirigenti che comprano lo
    stesso giorno nella stessa azienda sono un fatto solo, e contarli come
    cinque prove indipendenti gonfierebbe la statistica.
    """
    if not items:
        return None
    xs = sorted(x["car"] for x in items)
    n = len(xs)
    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    return {"n": n, "ep": len({(x["t"], x["d"]) for x in items}),
            "pos": sum(1 for x in xs if x > 0),
            "avg": round(sum(xs) / n, 2), "med": round(med, 2)}


def _dist(items, step=5, edge=30):
    """La distribuzione dei rendimenti in bucket da `step` punti.

    I due bucket agli estremi raccolgono le code: senza di loro un singolo
    -45% allargherebbe l'asse e schiaccerebbe tutto il resto.
    """
    bars = [{"lo": None, "hi": -edge, "n": 0}]
    bars += [{"lo": x, "hi": x + step, "n": 0} for x in range(-edge, edge, step)]
    bars.append({"lo": edge, "hi": None, "n": 0})
    for x in items:
        c = x["car"]
        if c < -edge:  bars[0]["n"] += 1
        elif c >= edge: bars[-1]["n"] += 1
        else:          bars[int((c + edge) // step) + 1]["n"] += 1
    return bars


def backtest(results, cfg):
    """Gli acquisti degli insider degli ultimi due anni, e come sono andati.

    Non è il backtest del punteggio: guidance e short interest non esistono
    giorno per giorno all'indietro, quindi il punteggio storico non si può
    ricostruire senza inventarlo. Qui si verifica la parte che i dati
    permettono di verificare davvero, cioè la più pesante: un acquisto di un
    insider ha anticipato qualcosa nei 60 giorni successivi, rispetto al suo
    settore? E le due regole che la formula applica — il cluster conta più di
    un acquisto isolato, un dirigente apicale più di un altro — reggono sui
    dati di questo universo?
    """
    rows = backtest_rows(results, cfg)
    if len(rows) < 10:
        return None
    # Dal 22 novembre 2026 l'unità è la campagna, non l'acquisto: vedi
    # pipeline/bonus.py. Contando gli acquisti, 16 compere di fila dello stesso
    # compratore valevano come 16 prove, e il rendimento mediano pubblicato
    # (+5,2%) era in gran parte l'eco di poche campagne contate molte volte.
    camps = bonus.campaigns(rows, cfg["rules"]["cluster_min_people"])
    pick = lambda f: [c for c in camps if f(c)]
    return {"horizon": cfg["rules"]["track_record_horizon_days"],
            "unit": "campaign", "days": bonus.CAMPAIGN_DAYS,
            "since": min(r["d"] for r in rows),
            "dist": _dist(camps),
            "all": bonus.summary(camps),
            "by_purchase": _stats(rows),     # il conteggio di prima, per confronto
            "cluster": bonus.summary(pick(lambda c: c["cluster"])),
            "solo": bonus.summary(pick(lambda c: not c["cluster"])),
            "senior": bonus.summary(pick(lambda c: c["senior"])),
            "other": bonus.summary(pick(lambda c: not c["senior"])),
            # dopo una trimestrale sopra le attese, o in linea e sotto (Alpha Vantage)
            "beat": bonus.summary(pick(lambda c: c.get("beat") is True)),
            "nobeat": bonus.summary(pick(lambda c: c.get("beat") is False)),
            # per dimensione: negli studi il segnale è più forte nelle società piccole
            "large": bonus.summary(pick(lambda c: (c.get("size") or "large") == "large")),
            "mid": bonus.summary(pick(lambda c: c.get("size") == "mid")),
            "small": bonus.summary(pick(lambda c: c.get("size") == "small")),
            "bonus": bonus.estimate(camps)}


def backtest_rows(results, cfg):
    """Ogni acquisto con 60 sedute già trascorse, e le sue caratteristiche.

    "Apicale" qui vuol dire esattamente quello che vuol dire nella formula
    (rules.is_senior, cioè CEO, CFO o presidente). Prima il backtest usava il
    flag "officer" del Form 4, che vale per qualunque dirigente — anche il
    responsabile legale o il capo contabile — e così metteva alla prova una
    regola diversa da quella che la formula applica.
    """
    nd = cfg["rules"]["cluster_days"]
    titles = cfg["rules"]["senior_titles"]
    rows = []
    for d in results:
        tx = [t for t in (d.get("tx") or []) if t["kind"] == "BUY"]
        car = {(w, b["date"]): b["car"]
               for w, tr in (d.get("tracks") or {}).items() for b in tr["buys"]}
        earn = d.get("_earn_long") or []
        for t in tx:
            r = car.get((t["who"], t["date"]))
            if r is None:
                continue                      # 60 sedute non ancora trascorse
            day = date.fromisoformat(t["date"])
            near = {x["who"] for x in tx
                    if abs((date.fromisoformat(x["date"]) - day).days) <= nd}
            ls = alphavantage.last_surprise(earn, t["date"]) if earn else None
            rows.append({"car": r, "t": d["company"]["ticker"], "d": t["date"],
                         "people": len(near),
                         "senior": rules.is_senior(t, titles),
                         # None se non si sa com'era andata l'ultima trimestrale
                         "beat": (ls["sp"] > 0) if ls else None,
                         "size": d.get("size") or "large"})
    return rows


def driver(subs):
    """La voce che ha spostato il punteggio più lontano da 50.

    Non è la voce con il valore più alto: è quella il cui scarto dal centro,
    moltiplicato per il suo peso, pesa di più sul totale. Serve all'elenco, dove
    il verdetto scritto finisce per ripetersi su quasi tutte le righe.
    """
    best = None
    for x in subs:
        if x.get("v") is None:
            continue
        push = abs(x["v"] - 50) * x["w"]
        if push > 0 and (best is None or push > best[0]):
            best = (push, x)
    if best is None:
        return None
    x = best[1]
    return {"name": x["name"], "up": x["v"] >= 50}


def card_view(d, months=CARD_TX_MONTHS, days=CARD_TX_DAYS, last_n=CARD_TX_LAST):
    """Quello che finisce nel file della scheda.

    I calcoli usano dieci anni di storia. Il sito, delle operazioni, legge
    solo due cose: quelle degli ultimi 90 giorni (il riassunto della voce
    insider) e le ultime 25 (la tabella). La scheda porta quelle, più il
    numero di operazioni degli ultimi due anni in `tx_n`. Con le due liste
    intere la scheda di Meta pesava quasi un megabyte, quasi tutto vendite
    programmate, e duecento schede così si riscriverebbero ogni giorno.
    Le chiavi che cominciano con `_` sono di lavoro e non escono mai dalla
    pipeline. Il dizionario in memoria non viene toccato: il backtest e le
    altre prove lo leggono dopo.
    """
    out = {k: v for k, v in d.items() if not k.startswith("_")}
    last = ((d.get("price") or {}).get("last") or {}).get("d")
    tx = d.get("tx") or []
    if last and tx:
        end = date.fromisoformat(last)
        cut = (end - timedelta(days=int(months * 30.44))).isoformat()
        near = (end - timedelta(days=days)).isoformat()
        two = [t for t in tx if t["date"] >= cut]
        keep = [t for t in two if t["date"] >= near]
        # `two` è in ordine di data, quindi quelle recenti sono la coda
        out["tx"] = two[-max(len(keep), last_n, 1):]
        out["tx_n"] = len(two)
    return out


def same_but_for_the_clock(path, data):
    """Il file su disco dice già la stessa cosa, a parte l'ora del giro?

    Senza questo controllo ogni giro riscrive tutte e 41 le schede anche quando
    non è successo niente, perché `generated_at` cambia sempre: git si ritrova
    41 file nuovi da conservare per un orario diverso. Con un giro all'ora
    diventerebbe il costo principale del progetto.
    """
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return False
    clock = ("generated_at", "changed_at")
    a = {k: v for k, v in old.items() if k not in clock}
    b = {k: v for k, v in data.items() if k not in clock}
    return a == b


def write_all(results, cfg, outdir=OUTDIR, forward=None, si_status=None, av=None, mk=None, periods=None):
    ddir = os.path.join(outdir, "data")
    os.makedirs(ddir, exist_ok=True)

    # Quanto regge la classifica se i pesi non sono quelli che ho scelto io.
    # Va fatto prima di scrivere le schede: ognuna si porta dietro la sua
    # fascia. Il calcolo è deterministico, quindi dati uguali, schede uguali.
    try:
        subs_by_t = {d["company"]["ticker"]:
                     {s["id"]: {"v": s["v"], "coverage": s.get("coverage")}
                      for s in (d["score"].get("subs") or [])}
                     for d in results}
        st = stress.run(subs_by_t, cfg["weights"], score.combine, score.verdict)
    except Exception as e:
        print(f"! prova dei pesi: {e}")
        st = None
    per = (st or {}).get("per") or {}

    # Le voci che si possono ricostruire nel passato, messe alla prova: vedi
    # pipeline/predict.py. Un errore qui non deve fermare il resto del giro.
    try:
        ic = predict.run(results, cfg)
    except Exception as e:
        print(f"! prova all'indietro delle voci: {e}")
        ic = None

    # Abituali o opportunisti, alla prova: pipeline/habits.py.
    try:
        hb = habits.run(results, cfg)
    except Exception as e:
        print(f"! abituali o opportunisti: {e}")
        hb = None

    # Che cosa succede dopo un acquisto, seduta per seduta: pipeline/eventstudy.py.
    try:
        ev = eventstudy.run(results, cfg)
    except Exception as e:
        print(f"! curva dopo un acquisto: {e}")
        ev = None

    # Il termometro degli insider sul mercato: pipeline/thermometer.py. Con i
    # data set della SEC (pipeline/mercato.py) anche su tutto il mercato, dal 2006.
    try:
        th = thermometer.run(results, market=(mk or {}).get("months"), market_px=(mk or {}).get("spy"),
                             through=(mk or {}).get("through"))
    except Exception as e:
        print(f"! termometro degli insider: {e}")
        th = None

    # Chi supera il 5% per contare: lo studio di evento dei 13D (pipeline/attivisti.py).
    try:
        act = attivisti.run(results)
    except Exception as e:
        print(f"! 13D: {e}")
        act = None

    # Il radar di tutto il mercato: un file a parte, site/radar.json, perché
    # sono centinaia di aziende e il sito lo legge solo nella sua pagina.
    rd = None
    if mk and mk.get("buys"):
        try:
            today = date.today().isoformat()
            ours = [d["company"]["ticker"] for d in results]
            comp = mercato.radar(mk["buys"], today, ours)
            radar_doc = {"today": today, "window": mercato.WINDOW, "min_value": mercato.MIN_VALUE,
                         "min_price": mercato.MIN_PRICE, "through": mk.get("through"),
                         "pending": len(mk.get("pending") or []), "read": mk.get("read") or 0,
                         "last_q": mk.get("last_q"), "companies": comp}
            rpath = os.path.join(outdir, "radar.json")
            if not same_but_for_the_clock(rpath, radar_doc):
                with open(rpath, "w", encoding="utf-8") as f:
                    json.dump(radar_doc, f, ensure_ascii=False, separators=(",", ":"))
            rd = {"n": len(comp), "clusters": sum(1 for c in comp if c["cluster"]),
                  "through": mk.get("through"), "pending": len(mk.get("pending") or []),
                  "top": [{k: c[k] for k in ("t", "name", "buyers", "value", "cluster", "ours")} for c in comp[:5]]}
        except Exception as e:
            print(f"! radar: {e}")

    # I grandi fondi: quali CUSIP sono stati riconosciuti per ogni azienda, per un controllo a mano.
    fd13 = None
    if periods:
        mp = fondi.mapping(periods)
        fd13 = {"quarters": len(periods), "from": min(periods), "to": max(periods),
                "found": len(mp), "missing": sorted(d["company"]["ticker"] for d in results
                                                    if d["company"]["ticker"] not in mp),
                "map": mp}

    # Gli avvisi: il feed RSS degli acquisti, site/feed.xml e site/feed-en.xml.
    try:
        fd = feed.write(results, outdir, cfg.get("site_url") or SITE_URL, cfg["rules"]["cluster_days"])
        print(f"  feed RSS: {fd['it'][0]} voci, {fd['it'][1]} nuove")
        fd = {"items": fd["it"][0]}
    except Exception as e:
        print(f"! feed RSS: {e}")
        fd = None

    rows, touched = [], 0
    for d in results:
        t = d["company"]["ticker"]
        if t in per:
            d["score"]["stress"] = per[t]
        path = os.path.join(ddir, f"{t}.json")
        card = card_view(d)
        if not same_but_for_the_clock(path, card):
            touched += 1
            with open(path, "w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False)
        s, ins = d["score"], d["insider_90d"]
        rows.append({
            "ticker": t, "name": d["company"]["name"],
            "group": d.get("group") or L("", ""),
            "score": s["value"], "verdict": s["verdict"],
            "confidence": s["confidence"], "covered_pct": s["covered_pct"],
            "driver": driver(s.get("subs") or []),
            "last": d["price"]["last"], "tx": d["counts"]["tx"],
            "buyers": ins["buyers"], "sellers": ins["sellers"],
            "net_musd": ins["net_musd"], "cluster": ins["cluster"],
            "errors": len(d["errors"]),
            "next_report": (d.get("next_report") or {}).get("date"),
        })
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ipath = os.path.join(outdir, "index.json")
    index = {
        "generated_at": now,          # quando abbiamo guardato
        "changed_at": now,            # quando qualcosa è cambiato davvero
        "groups": [{"nome": group_name(g), "benchmark": g["benchmark"],
                    "n": len(g["tickers"])} for g in cfg["universi"]],
        "backtest": backtest(results, cfg),
        "forward": forward,
        "weights": cfg["weights"],
        # l'insieme va nell'indice, le fasce delle singole aziende stanno
        # già nelle schede: nell'indice sarebbero quaranta righe in più
        "stress": {k: v for k, v in (st or {}).items() if k != "per"} or None,
        "ic": ic,
        # la curva media dopo un acquisto, dai 20 giorni prima ai 120 dopo
        "event": ev,
        # insider abituali o opportunisti (Cohen, Malloy e Pomorski 2012), alla prova
        "habits": hb,
        # quanti insider comprano e quanti vendono, tutte le aziende insieme, e che cosa
        # ha fatto la borsa dopo
        "thermometer": th,
        # il feed RSS degli acquisti (site/feed.xml): quante voci ha
        "feed": fd,
        # il radar di tutto il mercato (il dettaglio in site/radar.json)
        "radar": rd,
        # i 13D iniziali delle duecento aziende e che cosa ha fatto il titolo dopo
        "activists": act,
        # i grandi fondi (13F): da quando, e quale CUSIP per ogni azienda
        "funds": fd13,
        # salti di prezzo: gli scorpori corretti (config.json, corporate_actions) e i
        # giorni sotto il settore di più di 25 punti, da controllare a mano
        "price_jumps": {
            "adjusted": [dict(a, t=d["company"]["ticker"]) for d in results for a in d.get("_adjusted") or []],
            "flagged": [dict(j, t=d["company"]["ticker"]) for d in results for j in d.get("_jumps") or []],
        },
        # quante aziende hanno già lo storico dei Form 4 completo: con il
        # passaggio a cinque e poi a dieci anni la storia si riempie a rate, un giro dopo l'altro
        "history": {
            "complete": sum(1 for d in results if (d.get("history") or {}).get("tx_complete")),
            "total": len(results),
            "tx_from": min([h for h in ((d.get("history") or {}).get("tx_from") for d in results) if h],
                           default=None),
            "missing": sum((d.get("history") or {}).get("tx_missing") or 0 for d in results),
        },
        # da dove arriva lo short interest: FINRA per tutti appena possibile,
        # l'export manuale come riserva (vedi build_one e pipeline/finra.py)
        "short_interest": {
            "finra": sum(1 for d in results if d.get("si_source") == "finra"),
            "manual": sum(1 for d in results if d.get("si_source") == "manual"),
            "none": sum(1 for d in results if not d.get("si_source")),
            "from": (si_status or {}).get("from"),
            "to": (si_status or {}).get("to"),
            "readings": (si_status or {}).get("readings"),
            "pending": (si_status or {}).get("pending"),
            "error": (si_status or {}).get("error"),
            # l'intestazione del file solo se qualcosa non va: con dati buoni
            # cambierebbe l'indice a ogni giro senza dire niente
            "head": (si_status or {}).get("head") if not (si_status or {}).get("companies") else None,
        },
        # che cosa è arrivato da Alpha Vantage oltre alle trascrizioni: gli utili
        # si riempiono a rate, dodici aziende al giorno (vedi pipeline/alphavantage.py)
        "alphavantage": {
            "earnings": sum(1 for d in results if d.get("earnings")),
            "total": len(results),
            "calendar_on": (av or {}).get("calendar_on"),
            "listing_on": ((av or {}).get("listing") or {}).get("on"),
            "not_listed": sorted(d["company"]["ticker"] for d in results
                                 if alphavantage.listing_problem(av or {}, d["company"]["ticker"])),
        },
        "companies": rows,
        "errors": sorted({e for d in results for e in d["errors"]}),
    }
    # se nessuna scheda è cambiata e l'indice dice le stesse cose, teniamo la
    # data del cambiamento vero: il sito distingue "controllato" da "cambiato".
    if touched == 0 and same_but_for_the_clock(ipath, index):
        try:
            with open(ipath, encoding="utf-8") as f:
                index["changed_at"] = json.load(f).get("changed_at") or now
        except (OSError, ValueError):
            pass
    print(f"  schede riscritte: {touched} su {len(results)}")
    with open(ipath, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    old = os.path.join(outdir, "data.json")       # il file della versione a un titolo
    if os.path.exists(old):
        os.remove(old)
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="un solo ticker invece di tutti gli universi")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default=OUTDIR)
    a = ap.parse_args()
    cfg = load_config(a.config)

    groups = cfg["universi"]
    if a.ticker:
        t = a.ticker.upper()
        groups = [dict(g, tickers=[t]) for g in groups if t in g["tickers"]] or \
                 [{"nome": "Su richiesta", "nome_en": "On request",
                   "benchmark": groups[0]["benchmark"], "tickers": [t]}]

    print("Estrazione dalle trascrizioni")
    extracted = extract_all()

    results, bench_closes, groups_built = [], {}, []
    # Il tetto della storia vecchia: un numero di documenti e un orario. Il
    # primo tiene il carico sulla SEC, il secondo tiene il giro sotto l'ora,
    # perché il giro dell'ora dopo annullerebbe questo (vedi update.yml).
    budget = {"left": int(cfg.get("max_new_filings_per_run") or NEW_FILINGS_PER_RUN),
              "until": time.time() + 60 * float(cfg.get("history_minutes_per_run") or HISTORY_MINUTES)}
    if not a.offline:
        from . import sources
        sources.ARCHIVE_DIR = os.path.join(CACHE, "submissions")
    # Lo short interest di tutte le aziende, una rilevazione FINRA alla volta
    # (vedi pipeline/finra.py). Se FINRA non risponde il giro va avanti con la
    # cache e con gli export manuali.
    every_t = [t for g in groups for t in g["tickers"]]
    try:
        si_all, si_status = finra.update(os.path.join(CACHE, "finra"), every_t, offline=a.offline,
                                         files=int(cfg.get("finra_files_per_run") or finra.FILES_PER_RUN))
        print(f"Short interest FINRA: {si_status}")
    except Exception as e:
        # l'errore finisce nell'indice: è il solo modo di leggerlo senza aprire il log
        si_all, si_status = {}, {"error": str(e)[:300]}
        print(f"! short interest FINRA: {e}")
    # Utili, calendario e titoli da Alpha Vantage: li ha già scaricati il
    # workflow delle trascrizioni; se la cartella non c'è, tutto resta vuoto.
    av = alphavantage.load(AV_DIR)
    # Tutto il mercato e i grandi fondi: li ha scaricati il workflow mercato.yml
    # (pipeline/mercato.py e pipeline/fondi.py). Se la cartella non c'è, il
    # radar, il termometro di mercato e i 13F restano vuoti e il giro va avanti.
    try:
        mk = mercato.load(MK_DIR)
        print(f"Tutto il mercato: {len(mk['months'])} mesi, {len(mk['buys'])} acquisti, "
              f"coperto fino al {mk['through'] or '-'}, {len(mk['pending'])} giorni da leggere")
    except Exception as e:
        mk = None
        print(f"! tutto il mercato: {e}")
    try:
        periods = fondi.load(os.path.join(MK_DIR, "13f"))
        print(f"13F: {len(periods)} trimestri" + (f", l'ultimo al {max(periods)}" if periods else ""))
    except Exception as e:
        periods = {}
        print(f"! 13F: {e}")
    print(f"Alpha Vantage: utili di {len(av['earnings'])} aziende, calendario del "
          f"{av['calendar_on'] or '-'}, elenco dei titoli del {(av['listing'] or {}).get('on') or '-'}")
    for g in groups:
        print(f"\n=== {g['nome']} ({len(g['tickers'])} aziende) ===")
        bench = []
        if not a.offline:
            try:
                bench, bsrc = prices_or_copy(g["benchmark"])
                print(f"Riferimento {g['benchmark']}: {len(bench)} sedute da {bsrc}")
            except Exception as e:
                print(f"! riferimento {g['benchmark']}: {e}")
        for p in bench:
            bench_closes[(g["benchmark"], p["d"])] = p["c"]
        built = []
        for i, t in enumerate(g["tickers"], 1):
            print(f"\n[{i}/{len(g['tickers'])}] {t}")
            try:
                raw = gather(cfg, t, bench, a.offline, budget)
                if t.upper() in si_all:
                    raw["si"] = si_all[t.upper()]
                raw["earnings"] = av["earnings"].get(t.upper()) or []
                raw["calendar"] = av["next"].get(t.upper()) or []
                gone = alphavantage.listing_problem(av, t.upper())
                if gone:
                    raw["errors"] = list(raw.get("errors") or []) + [gone]
                mine = [e for e in extracted if e.get("ticker") == t.upper()]
                d = build_one(cfg, raw, mine)
                d["group"] = group_name(g)
                d["benchmark"] = g["benchmark"]
                d["size"] = g.get("dimensione") or "large"   # "mid" e "small": le aziende aggiunte il 24/11/2026
                d["_bench_long"] = bench          # per la curva "dopo un acquisto" (eventstudy.py)
                # i grandi fondi trimestre per trimestre (pipeline/fondi.py)
                d["_inst"] = fondi.series(periods, t.upper())
                d["inst"] = d["_inst"][-CARD_INST_Q:] or None
                built.append(d)
            except Exception as e:
                print(f"  !! {t} saltato: {e}")
        groups_built.append(built)

    # Seconda passata. I bonus della voce insider si stimano sugli acquisti
    # passati di tutte le aziende insieme, quindi si possono applicare solo
    # dopo averle costruite tutte. Poi il settore, che si calcola per gruppo.
    every = [d for b in groups_built for d in b]
    est = bonus.estimate(bonus.campaigns(backtest_rows(every, cfg),
                                         cfg["rules"]["cluster_min_people"]))
    pts = bonus.points(est)
    print(f"\nBonus insider stimati dai dati: {pts} (a mano erano {bonus.HAND})")
    for d in every:
        ins = d["_subs"].get("insider") or {}
        if ins.get("v") is None or "_ins" not in d:
            continue
        v, why, cov = score.insider_score(d["tx"], d["_ins"]["cluster"], d["tracks"], cfg,
                                          date.fromisoformat(d["_ins"]["today"]), bonus=pts,
                                          beat=d["_ins"].get("beat"))
        d["_subs"]["insider"] = {"v": v, "why": why, "coverage": cov}
    for built in groups_built:
        universe = [d["insider_90d"] for d in built]
        results += [finish(cfg, d, universe) for d in built]

    def one_line(v):                      # per la console: una lingua basta
        return v["it"] if isinstance(v, dict) else str(v)

    hpath = os.path.join(a.out, "history.csv")
    fwd = None
    try:
        before = history.load(hpath)
        snap = history.snapshot(results, bench_closes)
        added = history.add(hpath, snap, known=before)
        # Il riassunto si fa sul registro come è adesso, senza rileggerlo da
        # disco: le righe di oggi sono quelle appena aggiunte, non tutte quelle
        # della fotografia (una riga già presente non si duplica).
        seen = {(r["date"], r["ticker"]) for r in before}
        rows = before + [r for r in snap if (r["date"], r["ticker"]) not in seen]
        fwd = history.stats(rows)
        print(f"\nRegistro delle previsioni: +{added} righe, {fwd['observations']} in tutto, "
              f"{fwd['done']} chiuse, {fwd['pending']} in attesa")
    except Exception as e:
        print(f"! registro delle previsioni: {e}")

    index = write_all(results, cfg, a.out, forward=fwd, si_status=si_status, av=av, mk=mk, periods=periods)
    total = sum(len(g["tickers"]) for g in groups)
    print(f"\n{len(results)} aziende su {total}")
    for r in index["companies"][:12]:
        v = r["score"] if r["score"] is not None else "--"
        flag = " cluster" if r["cluster"] else ""
        print(f"  {str(v):>3}  {r['ticker']:<6} {one_line(r['verdict']):<32} "
              f"{r['buyers']}c/{r['sellers']}v  {r['net_musd']:+.2f} M${flag}")
    if index["errors"]:
        print("\nPROBLEMI:")
        for e in index["errors"]:
            print("  -", e)
    print(f"\nScritti {a.out}/index.json e {a.out}/data/*.json")


if __name__ == "__main__":
    main()
