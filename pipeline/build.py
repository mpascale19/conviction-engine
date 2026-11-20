"""Orchestratore: raccoglie i dati di tutto l'universo, applica le regole,
calcola i punteggi e scrive i file che il sito legge.

    python -m pipeline.build                  # tutti i ticker di config.json
    python -m pipeline.build --ticker AEHR    # uno solo
    python -m pipeline.build --offline        # ricalcola dalla cache, senza rete

Scrive:
    site/index.json          l'elenco con punteggio e riassunto di ogni azienda
    site/data/<TICKER>.json  il dettaglio di una singola azienda
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone

from . import market, rules, score
from .rules import L
from .extract import extract_all

CACHE = "cache"
OUTDIR = "site"
EVENT_DAYS = 180        # finestra dei fatti rilevanti 8-K: due trimestri


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
def gather(cfg, ticker, bench, offline=False):
    """Prezzi e Form 4 di un ticker. Non solleva: registra gli errori."""
    raw_path = os.path.join(CACHE, f"raw_{ticker}.json")
    if offline:
        with open(raw_path, encoding="utf-8") as f:
            return json.load(f)

    from . import sources
    errors, failed, px, tx, src = [], [], [], [], None
    name, cik, shares, sh_date = ticker, "", None, None

    try:
        px, src = sources.prices(ticker)
        print(f"  prezzi: {len(px)} sedute da {src}, ultima {px[-1]['d']}")
    except Exception as e:
        errors.append(f"Prezzi di {ticker}: {e}")
        failed.append("prices")
        print(f"  ! prezzi: {e}")

    try:
        fallback = (cfg.get("cik") or {}).get(ticker)
        cik, name, how = sources.cik_for(ticker, fallback)
        print(f"  CIK {cik} ({name}) via {how}")
        tx = sources.insider_transactions(cik, cfg["months_of_history"],
                                          limit=cfg.get("max_filings_per_ticker"),
                                          cache_dir=os.path.join(CACHE, "form4"))
        print(f"  Form 4: {len(tx)} operazioni")
    except Exception as e:
        errors.append(f"Form 4 di {ticker}: {e}")
        failed.append("tx")
        print(f"  ! Form 4: {e}")

    events = None
    if cik:                               # azioni in circolazione: dalla copertina dei 10-Q/10-K
        shares, sh_date = sources.shares_outstanding(cik)
        if shares:
            print(f"  azioni in circolazione: {shares/1e6:.0f} milioni (al {sh_date})")
        try:                              # fatti rilevanti: stesso JSON, nessuna richiesta in più
            since = (date.today() - timedelta(days=EVENT_DAYS)).isoformat()
            events = sources.recent_8k(cik, since)
            print(f"  8-K negli ultimi {EVENT_DAYS} giorni: {len(events)}")
        except Exception as e:
            errors.append(f"8-K di {ticker}: {e}")
            print(f"  ! 8-K: {e}")

    raw = {"ticker": ticker, "name": name, "cik": cik, "prices": px, "bench": bench,
           "tx": tx, "errors": errors, "failed": failed, "price_source": src,
           "shares": shares, "shares_date": sh_date, "events": events,
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    os.makedirs(CACHE, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    return raw


# ----------------------------------------------------------------- calcolo
def build_one(cfg, raw, extracted=None):
    px = raw.get("prices") or []
    bench = raw.get("bench") or []
    tx = rules.enrich(sorted(raw.get("tx") or [], key=lambda t: t["date"]))
    R, W = cfg["rules"], cfg["weights"]
    failed = raw.get("failed") or []
    today = date.fromisoformat(px[-1]["d"]) if px else date.today()

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
        ("market", L("Servono revisioni delle stime e short interest (Bloomberg).",
                     "Needs estimate revisions and short interest (Bloomberg).")),
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

    si = market.load_short_interest(raw["ticker"])
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
        "si_pct": si_pct,
        "shares": raw.get("shares"),
        "tx": tx,
        "cluster": [t["date"] + " " + t["who"] for t in cluster],
        "tracks": tracks,
        "_subs": subs,
    }


def finish(cfg, data, universe):
    """Secondo passaggio: il settore si calcola solo contro i simili.

    `universe` sono i riassunti delle aziende dello stesso gruppo: confrontare
    un produttore di chip con una catena di supermercati non direbbe niente.
    """
    subs = data.pop("_subs")
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
         "m": L("Bloomberg, export manuale", "Bloomberg, manual export"),
         "x": L(f"{data['counts'].get('si', 0)} rilevazioni bimensili.",
                f"{data['counts'].get('si', 0)} twice-monthly readings.")}
        if data["counts"].get("si") else
        {"n": 3, "t": L("Short interest", "Short interest"),
         "m": L("non caricato", "not loaded"),
         "x": L("Serve l'export di Bloomberg in input/market/.",
                "Needs the Bloomberg export in input/market/.")},
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
    nd = cfg["rules"]["cluster_days"]
    minp = cfg["rules"]["cluster_min_people"]
    rows = []
    for d in results:
        tx = [t for t in (d.get("tx") or []) if t["kind"] == "BUY"]
        car = {(w, b["date"]): b["car"]
               for w, tr in (d.get("tracks") or {}).items() for b in tr["buys"]}
        for t in tx:
            r = car.get((t["who"], t["date"]))
            if r is None:
                continue                      # 60 giorni non ancora trascorsi
            day = date.fromisoformat(t["date"])
            near = {x["who"] for x in tx
                    if abs((date.fromisoformat(x["date"]) - day).days) <= nd}
            rows.append({"car": r, "t": d["company"]["ticker"], "d": t["date"],
                         "cluster": len(near) >= minp,
                         "senior": bool(t.get("is_senior"))})
    if len(rows) < 10:
        return None
    pick = lambda f: [r for r in rows if f(r)]
    return {"horizon": cfg["rules"]["track_record_horizon_days"],
            "all": _stats(rows),
            "cluster": _stats(pick(lambda r: r["cluster"])),
            "solo": _stats(pick(lambda r: not r["cluster"])),
            "senior": _stats(pick(lambda r: r["senior"])),
            "other": _stats(pick(lambda r: not r["senior"]))}


def write_all(results, cfg, outdir=OUTDIR):
    ddir = os.path.join(outdir, "data")
    os.makedirs(ddir, exist_ok=True)
    rows = []
    for d in results:
        t = d["company"]["ticker"]
        with open(os.path.join(ddir, f"{t}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        s, ins = d["score"], d["insider_90d"]
        rows.append({
            "ticker": t, "name": d["company"]["name"],
            "group": d.get("group") or L("", ""),
            "score": s["value"], "verdict": s["verdict"],
            "confidence": s["confidence"], "covered_pct": s["covered_pct"],
            "last": d["price"]["last"], "tx": d["counts"]["tx"],
            "buyers": ins["buyers"], "sellers": ins["sellers"],
            "net_musd": ins["net_musd"], "cluster": ins["cluster"],
            "errors": len(d["errors"]),
        })
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "groups": [{"nome": group_name(g), "benchmark": g["benchmark"],
                    "n": len(g["tickers"])} for g in cfg["universi"]],
        "backtest": backtest(results, cfg),
        "weights": cfg["weights"],
        "companies": rows,
        "errors": sorted({e for d in results for e in d["errors"]}),
    }
    with open(os.path.join(outdir, "index.json"), "w", encoding="utf-8") as f:
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

    results = []
    for g in groups:
        print(f"\n=== {g['nome']} ({len(g['tickers'])} aziende) ===")
        bench = []
        if not a.offline:
            from . import sources
            try:
                bench, bsrc = sources.prices(g["benchmark"])
                print(f"Riferimento {g['benchmark']}: {len(bench)} sedute da {bsrc}")
            except Exception as e:
                print(f"! riferimento {g['benchmark']}: {e}")
        built = []
        for i, t in enumerate(g["tickers"], 1):
            print(f"\n[{i}/{len(g['tickers'])}] {t}")
            try:
                raw = gather(cfg, t, bench, a.offline)
                mine = [e for e in extracted if e.get("ticker") == t.upper()]
                d = build_one(cfg, raw, mine)
                d["group"] = group_name(g)
                built.append(d)
            except Exception as e:
                print(f"  !! {t} saltato: {e}")
        universe = [d["insider_90d"] for d in built]
        results += [finish(cfg, d, universe) for d in built]

    def one_line(v):                      # per la console: una lingua basta
        return v["it"] if isinstance(v, dict) else str(v)

    index = write_all(results, cfg, a.out)
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
