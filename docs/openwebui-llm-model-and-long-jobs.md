# Open WebUI: volba LLM modelu a vícedenní úlohy

## Volba modelu

AutoGenBook Pipe přidává do uživatelských `UserValves` pole **LLM model** typu `select`.
Seznam se sestavuje z modelů registrovaných v Open WebUI; jako bezpečný fallback je vždy
k dispozici `e-infra.glm-5`, který je současně výchozí hodnotou.

Vybraný identifikátor se zapisuje do specifikace úlohy při jejím vytvoření. Companion jej
persistuje společně s úlohou a při spuštění nastaví:

- `AUTOGENBOOK_LLM_MODEL`,
- `OPENWEBUI_MODEL`,
- `OPENAI_MODEL`,
- `OPENROUTER_MODEL`,
- `LLM_MODEL`,
- `MODEL_NAME`.

Runtime AutoGenBooku navíc vynucuje stejný model na vrstvě OpenAI kompatibilního klienta.
Model proto zůstane stejný po celou dobu úlohy, i když uživatel mezitím přepne model v jiné
konverzaci.

## Vícedenní běh

Generování běží v samostatném procesu Companionu. Pipe vypíná staré volby blokující chat
(`wait_for_completion`, `synchronous` a obdobné), pokud je daná verze integrace obsahuje.
Ukončení HTTP požadavku nebo zavření konverzace tedy úlohu nezastaví.

Stav je ukládán atomicky do souboru `.autogenbook-progress.json` v adresáři úlohy. Obsahuje:

- procenta a aktuální fázi;
- text posledního kroku;
- zvolený model;
- `started_at`, `updated_at` a `heartbeat_at`;
- PID procesu;
- volitelně `current` a `total`.

Heartbeat se obnovuje standardně každých 30 sekund. Companion poskytuje stav na endpointu:

```text
GET /api/v1/autogenbook/jobs/{job_id}/progress
```

V Open WebUI lze stav znovu načíst příkazem:

```text
/autogenbook status JOB_ID
```

`UserValves` obsahují také interval aktualizace a dobu sledování v popředí. Po jejím uplynutí
chat vrátí ID úlohy, zatímco proces pokračuje na pozadí.

## Velké výsledky

Companion neskrývá soubor jen proto, že překročil dřívější velikostní práh. Soubory jsou
získávány přímo z adresáře úlohy a seznam je dostupný na:

```text
GET /api/v1/autogenbook/jobs/{job_id}/files
```

Každá položka obsahuje velikost a časově omezený podepsaný odkaz. Samotné stahování:

```text
GET /api/v1/autogenbook/jobs/{job_id}/files/{relative_path}
```

probíhá po blocích bez načtení celého souboru do paměti a podporuje `Range`. Přerušený
přenos lze proto obnovit; odpověď na částečný požadavek je `206 Partial Content` a obsahuje
`Content-Range`, `Content-Length` a `Accept-Ranges: bytes`.

Podepsané odkazy neobsahují trvalé přihlašovací údaje. Po expiraci se v Open WebUI příkazem
`/autogenbook status JOB_ID` vygenerují nové.

## Kontroly sestavení

Workflow ověřuje:

1. výchozí a explicitně zvolený model;
2. propagaci modelu do procesu AutoGenBook;
3. perzistenci progressu a heartbeatu;
4. registraci velkého řídkého testovacího souboru;
5. stažení konkrétního byte range a odpověď `206`;
6. syntaxi všech upravených modulů;
7. validitu výsledné Open WebUI Pipe;
8. deterministické přebalení integračního bundle a jeho SHA-256.
