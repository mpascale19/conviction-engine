"""Il passo dell'LLM: estrarre fatti dai testi, non decidere.

Legge i file di testo in input/transcripts/ (gli export delle earnings call
da Bloomberg) e chiede al modello di restituire campi strutturati: guidance,
tono, direttezza delle risposte. Nessun punteggio: quello lo calcola score.py.

Serve la variabile d'ambiente ANTHROPIC_API_KEY. Se manca, il passo viene
saltato e la pipeline va avanti con i soli dati pubblici.
"""
import glob
import hashlib
import json
import os

import requests

API = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("EXTRACT_MODEL", "claude-haiku-5-5")
CACHE = "cache"

PROMPT = """Sei un estrattore di dati. Leggi la trascrizione e restituisci SOLO un oggetto JSON,
senza testo intorno, con questa forma esatta:

{"quarter": "T3 '26",
 "tone": 0-100,
 "tone_why": "una frase",
 "guidance": [{"metric": "...", "value": "...", "direction": "alzata|ridotta|confermata|prima"}],
 "qa": [{"question": "...", "answer_summary": "...", "directness": 1-5}],
 "language_changes": [{"topic": "...", "from": "...", "to": "...", "note": "..."}]}

Regole: riporta solo quello che il testo dice davvero; se un campo non c'è, usa null o una
lista vuota; "tone" è una misura del linguaggio usato dal management, non un giudizio sul titolo;
"directness" dice se la risposta risponde alla domanda (5) o la aggira (1).

TRASCRIZIONE:
"""


def _cache_path(text):
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    return os.path.join(CACHE, f"extract_{h}.json")


def extract_text(text, api_key):
    """Una chiamata al modello. Il risultato viene messo in cache su disco:
    la stessa trascrizione non si paga due volte."""
    path = _cache_path(text)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    r = requests.post(API, timeout=120,
                      headers={"x-api-key": api_key,
                               "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": MODEL, "max_tokens": 4000,
                            "messages": [{"role": "user",
                                          "content": PROMPT + text[:120000]}]})
    r.raise_for_status()
    raw = r.json()["content"][0]["text"].strip()
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    data = json.loads(raw)
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def extract_all(folder="input/transcripts"):
    key = os.environ.get("ANTHROPIC_API_KEY")
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    if not files:
        print("  nessuna trascrizione in", folder)
        return []
    if not key:
        print("  ANTHROPIC_API_KEY non impostata: salto l'estrazione")
        return []
    out = []
    for p in files:
        with open(p, encoding="utf-8") as f:
            text = f.read()
        try:
            d = extract_text(text, key)
            d["file"] = os.path.basename(p)
            out.append(d)
            print("  estratto", os.path.basename(p))
        except Exception as e:
            print(f"  ! {os.path.basename(p)}: {e}")
    return out
