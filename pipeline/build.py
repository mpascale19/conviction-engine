"""Orchestratore: raccoglie, applica le regole, calcola e scrive site/data.json.

    python -m pipeline.build                 # usa config.json
    python -m pipeline.build --ticker AEHR   # forza un ticker
    python -m pipeline.build --offline       # rilegge cache/raw.json, senza rete
"""
import argparse
import json
import os
from datetime import date, datetime, timezone

from . import rules, score
from .extract import extract_all

CACHE = "cache"
OUT = "site/data.json"


def load_config(path="config.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def gather(cfg, offline=False):
    """Scarica (o rilegge dalla cache) prezzi, benchmark e Form 4."""
    raw_path = os.path.join(CACHE, "raw.json")
    if offline:
        with open(raw_path, encoding="utf-8") as f:
            return json.load(f)
    from . import sources                      # importato qui: serve la rete
    t, b = cfg["ticker"], cfg["benchmark"]
    errors, px, bench, tx, src = [], [], [], [], None
    failed = []
    name, cik = t, ""

    if sources.SEC_UA_IS_DEFAULT:
        errors.append("Il secret SEC_UA non e' impostato: EDGAR rifiuta le richieste anonime.")
        print("  ! SEC_UA non impostato")

    try:
        px, src = sources.prices(t)
        print(f"- prezzi {t}: {len(px)} sedute da {src}, ultima {px[-1]['d']}")
    except Exception as e:
        errors.append(f"Prezzi di {t}: {e}")
        failed.append("prices")
        print(f"  ! prezzi {t}: {e}")
    try:
        bench, bsrc = sources.prices(b)
        print(f"- prezzi del settore {b}: {len(bench)} sedute da {bsrc}")
    except Exception as e:
        errors.append(f"Prezzi del settore {b}: {e}")
        print(f"  ! prezzi {b}: {e}")
    try:
        cik, name = sources.cik_for(t)
        print(f"- CIK di {t}: {cik} ({name})")
        tx = sources.insider_transactions(cik, cfg["months_of_history"])
        print(f"- Form 4: {len(tx)} operazioni")
    except Exception as e:
        errors.append(f"Form 4 di {t}: {e}")
        failed.append("tx")
        print(f"  ! Form 4: {e}")

    raw = {"ticker": t, "name": name, "cik": cik,
           "prices": px, "bench": bench, "tx": tx, "errors": errors, "failed": failed,
           "price_source": src,
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    os.makedirs(CACHE, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    return raw


def build(cfg, raw, extracted=None):
    px, bench = raw.get("prices") or [], raw.get("bench") or []
    tx = rules.enrich(sorted(raw["tx"], key=lambda t: t["date"]))
    R = cfg["rules"]
    today = date.fromisoformat(px[-1]["d"]) if px else date.today()
    errors = list(raw.get("errors") or [])
    win_start = (today.replace(day=1) if False else today).isoformat()

    cluster = rules.find_cluster(tx, R["cluster_days"], R["cluster_min_people"])
    tracks = rules.track_records(tx, px, bench, R["track_record_horizon_days"],
                                 R["track_record_prior"],
                                 window_start=cluster[0]["date"] if cluster else None)

    subs = {}
    failed = raw.get("failed") or []
    if "tx" in failed:
        subs["insider"] = {"v": None, "coverage": "Assente",
                           "why": ["I Form 4 non sono stati scaricati: vedi gli errori in fondo."]}
    else:
        v, why, cov = score.insider_score(tx, cluster, tracks, cfg, today)
        subs["insider"] = {"v": v, "why": why, "coverage": cov}
    if px and "prices" not in failed:
        v, why, cov = score.price_score(px, today)
    else:
        v, why, cov = None, ["Nessun prezzo scaricato: vedi gli errori in fondo."], "Assente"
    subs["price"] = {"v": v, "why": why, "coverage": cov}

    # Fonti che oggi non sono pubbliche: restano vuote e il peso si ridistribuisce.
    for k, note in [("guid", "Serve l'export delle trascrizioni e della guidance."),
                    ("market", "Servono revisioni delle stime e short interest (Bloomberg)."),
                    ("sector", "Servono i Form 4 dei concorrenti: aggiungili in config."),
                    ("news", "Serve una fonte di notizie."),
                    ("social", "Serve una fonte di post.")]:
        subs[k] = {"v": None, "why": [note], "coverage": "Assente"}

    if extracted:
        tones = [e.get("tone") for e in extracted if isinstance(e.get("tone"), (int, float))]
        if tones:
            subs["guid"] = {"v": round(sum(tones[-4:]) / len(tones[-4:])),
                            "why": [f"Tono medio delle ultime {len(tones[-4:])} call estratte "
                                    f"dalle trascrizioni: {round(sum(tones[-4:]) / len(tones[-4:]))}."]
                                   + [e.get("tone_why") for e in extracted[-2:] if e.get("tone_why")],
                            "coverage": "Media"}

    total, conf, missing, covered = score.combine(subs, cfg["weights"])
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "errors": errors,
        "price_source": raw.get("price_source"),
        "company": {"ticker": raw["ticker"], "name": raw["name"], "cik": raw["cik"]},
        "price": {"last": px[-1] if px else None,
                  "series": [[p["d"], p["c"]] for p in px[-504:]]},
        "counts": {"tx": len(tx), "prices": len(px), "bench": len(bench)},
        "tx": [t for t in tx if t["date"] >= (px[0]["d"] if px else "")],
        "cluster": [t["date"] + " " + t["who"] for t in cluster],
        "tracks": tracks,
        "score": {"value": total,
                  "verdict": score.verdict(total) if total is not None else "Dati insufficienti",
                  "confidence": conf, "covered_pct": covered, "missing": missing,
                  "subs": [{"id": k, "name": score.NAMES[k], "w": cfg["weights"][k],
                            **subs[k]} for k in cfg["weights"]]},
        "sources": [
            {"n": 1, "t": "Form 4", "m": f"SEC EDGAR, CIK {raw['cik']}",
             "x": f"{len(tx)} operazioni negli ultimi {cfg['months_of_history']} mesi."},
            {"n": 2, "t": "Prezzi di chiusura", "m": raw.get("price_source") or "-",
             "x": f"{len(px)} sedute, ultima {px[-1]['d'] if px else '-'}."},
            {"n": 3, "t": "Settore", "m": f"{raw.get('price_source') or '-'}, {cfg['benchmark']}",
             "x": "Usato come riferimento per i rendimenti anomali."},
        ],
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.ticker:
        cfg["ticker"] = a.ticker.upper()
    print(f"Conviction Engine - {cfg['ticker']}")
    raw = gather(cfg, a.offline)
    print("- estrazione dalle trascrizioni")
    extracted = extract_all()
    data = build(cfg, raw, extracted)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    s = data["score"]
    if s["value"] is None:
        print("\nNessun punteggio: non e' arrivata nessuna fonte.")
    else:
        print(f"\nScore {s['value']} ({s['verdict']}), confidenza {s['confidence'].lower()}, "
              f"copertura {s['covered_pct']}%")
    if s["missing"]:
        print("Sotto-punteggi senza dati:", ", ".join(s["missing"]))
    if data["errors"]:
        print("\nPROBLEMI DA GUARDARE:")
        for e in data["errors"]:
            print("  -", e)
    print("\nScritto", a.out)


if __name__ == "__main__":
    main()
