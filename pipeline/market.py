"""Dati di mercato che arrivano da un export manuale (Bloomberg).

Per ora solo lo short interest. Il file sta in input/market/<TICKER>_si.csv con
queste colonne: date, short_interest, close, avg_volume, days_to_cover.
Niente rete: è un file che l'utente mette lì.
"""
import csv
import os

FOLDER = "input/market"


def load_short_interest(ticker, folder=FOLDER):
    """Le rilevazioni di un titolo, in ordine di data. Lista vuota se non c'è."""
    path = os.path.join(folder, f"{ticker.upper()}_si.csv")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"date": r["date"],
                            "si": float(r["short_interest"]),
                            "close": float(r["close"]),
                            "vol": float(r.get("avg_volume") or 0),
                            "dtc": float(r.get("days_to_cover") or 0)})
            except (TypeError, ValueError):
                continue
    out.sort(key=lambda x: x["date"])
    return out
