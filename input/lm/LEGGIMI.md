# Le parole di Loughran e McDonald

Tre elenchi del dizionario finanziario di Tim Loughran e Bill McDonald
(«When Is a Liability Not a Liability? Textual Analysis, Dictionaries, and
10-Ks», *Journal of Finance*, 2011), una parola per riga, in minuscolo:

- `positive.txt`: 354 parole positive;
- `negative.txt`: 2.355 parole negative;
- `uncertainty.txt`: 297 parole di incertezza.

Li usa `pipeline/comunicati.py` per misurare il tono delle frasi dei dirigenti
nei comunicati. È il dizionario standard della finanza: quelli generici
contano come negative parole come «tax», «cost» o «liability», che in un
bilancio sono neutre.

Fonte: la copia del Master Dictionary (versione 2012) distribuita con il
pacchetto Python `pysentiment2` (github.com/nickderobertis/pysentiment).
Gli autori lo rendono disponibile per la ricerca accademica:
https://sraf.nd.edu/loughranmcdonald-master-dictionary/ .
