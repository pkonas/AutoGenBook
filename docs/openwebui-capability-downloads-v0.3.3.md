# AutoGenBook Open WebUI 0.3.3

## Oprava stahování výstupů v Open WebUI Desktop

Tato verze nahrazuje nefunkční přihlašovací „landing page + download ticket“ tok z verze 0.3.2 přímým, kryptograficky podepsaným download odkazem pro každý jednotlivý výstupní soubor.

## Potvrzená příčina incidentu

Serverový log z 18. září 2026 ukazuje konzistentní sekvenci:

```text
GET  /api/autogenbook/files/<file-id>/download  → 200
POST /api/autogenbook/download-ticket           → 401
```

Stejný `POST` byl opakován několikrát a endpoint `/api/autogenbook/download-stream/...` nebyl vůbec zavolán. Výstupní data přitom existovala: Companion úspěšně vrátil artefakt a Open WebUI dokázalo načíst jeho databázový záznam.

Důvodem je izolace úložiště Electron webview. Hlavní webview Open WebUI mělo platný token, ale landing stránka otevřená jako nové okno nebo externí navigace nemusela sdílet jeho `localStorage` ani session cookie. JavaScript landing stránky proto poslal ticket request bez použitelného bearer tokenu a Open WebUI jej správně odmítlo stavem 401.

Překreslení dialogu, doplnění textu `data.content` ani další retry tento problém nemohly vyřešit, protože chybný byl samotný autentizační přechod mezi okny.

## Nový download kontrakt

Výstupní odkaz má tvar:

```text
/api/autogenbook/v033/output/<file-id>/<filename>?cap=<hmac-capability>
```

Odkaz:

- nevyžaduje přístup k `localStorage`;
- nevyžaduje session cookie v novém okně;
- neobsahuje Open WebUI API klíč;
- neobsahuje browserový session token;
- neobsahuje AutoGenBook Companion token;
- neobsahuje náhodný port Companionu;
- nemá časovou expiraci;
- zůstává funkční po restartu Companionu i Open WebUI.

HMAC capability je svázána s:

- ID souboru v Open WebUI;
- vlastníkem souboru;
- přesným názvem souboru;
- tajným klíčem uloženým mimo zdroj Function.

Route před odesláním bajtů znovu ověřuje existenci databázového záznamu, vlastníka, název souboru a fyzická data. Smazání souboru odkaz okamžitě zneplatní. Rotace secret souboru zneplatní všechny dřívější odkazy.

### Bezpečnostní vlastnost odkazu

Capability URL je sama přístupovým oprávněním k jednomu konkrétnímu souboru. Kdo získá celý odkaz, může daný soubor stáhnout, dokud existuje a secret není změněn. Odkaz proto nemá být zveřejňován mimo oprávněnou konverzaci. Tento model je záměrný: řeší Electron kontexty, které nepřenášejí autentizaci do nově otevřeného okna, aniž by do URL vkládal univerzální uživatelský nebo administrátorský klíč.

## Velké soubory

Přímý endpoint podporuje:

- `GET`;
- `HEAD`;
- `Range`;
- `If-Range`;
- `ETag`;
- `206 Partial Content`;
- `416 Range Not Satisfiable`;
- blokové streamování po 1 MiB.

Celý soubor se nenačítá do RAM. Přerušený download lze obnovit, pokud jej podporuje klient.

## Instalace aktualizace na Windows

### 1. Záloha dat

Před aktualizací vytvořte kopii adresáře:

```text
C:\Users\<uživatel>\AppData\Local\Programs\AutoGenBook OpenWebUI\data
```

Aktualizace tento adresář nemaže. Obsahuje projekty, KB1/KB2, úlohy, checkpointy, progress, logy a vytvořené artefakty.

### 2. API klíč v TXT souboru

Vytvořte například:

```text
C:\Users\<uživatel>\.secrets\openwebui-api-key.txt
```

Soubor má obsahovat jeden administrátorský Open WebUI API klíč. Instalátor čte pouze jeho obsah; klíč nekopíruje do projektu ani do reportu.

### 3. Spuštění

Rozbalte celý projektový ZIP a spusťte:

```cmd
Install-AutoGenBook.cmd ^
  --openwebui-url "http://127.0.0.1:8080" ^
  --webui-api-key-file "C:\Users\<uživatel>\.secrets\openwebui-api-key.txt"
```

Port nahraďte skutečným portem lokální instance Open WebUI Desktop, pokud není 8080.

Instalátor:

1. načte Function `autogenbook_companion` přes admin API;
2. uloží zálohu původního zdroje;
3. odstraní landing/ticket tok 0.3.2;
4. vloží přímou capability route 0.3.3;
5. ověří Python syntaxi a bezpečnostní kontrakt;
6. uloží aktualizovanou Function přes admin API;
7. znovu ji načte a ověří skutečně persistovaný obsah;
8. vytvoří `dist/AutoGenBook-OpenWebUI-Function-v0.3.3.py` pro audit nebo ruční import.

### 4. Úplný restart

Po aktualizaci ukončete celý Open WebUI Desktop, ověřte, že nezůstal běžet v oznamovací oblasti, a aplikaci znovu spusťte. Pouhé obnovení stránky nestačí, protože stará Function i staré API trasy mohou být stále načtené v procesu backendu.

## Oprava už vytvořených úloh

Není nutné znovu volat LLM ani generovat dokument.

V AutoGenBook chatu odešlete:

```text
výstupy <job-id>
```

Například:

```text
výstupy c1e7de76-c63b-4915-a748-fc161e6ea2ee
```

Function:

- načte existující artefakty;
- použije stejné deterministické Open WebUI file ID;
- přepíše starý preview text;
- vytvoří nový capability odkaz;
- odešle novou odpověď se souborovými kartami;
- nevytvoří duplicitní kopie.

Staré zprávy s odkazy 0.3.2 se zpětně nemění. Použijte novou odpověď po příkazu `výstupy`.

## Diagnostika

Po správné aktualizaci má serverový log při kliknutí obsahovat například:

```text
GET /api/autogenbook/v033/output/<file-id>/<filename>?cap=... HTTP/1.1 200
```

nebo při pokračování přenosu:

```text
GET /api/autogenbook/v033/output/<file-id>/<filename>?cap=... HTTP/1.1 206
```

V logu se už nesmí objevovat:

```text
POST /api/autogenbook/download-ticket HTTP/1.1 401
```

### Kontrola Function

V **Admin Panel → Functions → AutoGenBook Companion** musí být:

```text
version: 0.3.3
```

a ve zdroji musí být:

```text
_AGB_DOWNLOAD_PREFIX = "/api/autogenbook/v033"
```

## Ruční aktualizace Function

Automatická aktualizace vyžaduje administrátorský API klíč. Při nedostatečném oprávnění zůstane v adresáři `dist` kompletní Function 0.3.3. Její celý obsah lze vložit do stávající Function v Open WebUI a poté aplikaci úplně restartovat.

## Testovací kontrakt

Release gate ověřuje:

- odstranění ticket a landing endpointů;
- stažení bez `Authorization` hlavičky a bez cookie;
- odmítnutí změněné capability;
- odmítnutí nesprávného názvu souboru;
- persistenci capability secretu;
- GET, HEAD, Range, suffix Range, 206 a 416;
- vložení route před SPA catch-all;
- blokové streamování;
- zneplatnění po smazání dat;
- Python syntaxi patcheru a instalátoru;
- deterministický projektový ZIP a TAR.GZ;
- SHA-256 inventář celého projektu.

## Poznámka k přiloženým logům

Desktopový log obsahuje hodnotu API klíče jiné lokální komponenty v čitelné podobě. Tento klíč je vhodné po sdílení logu změnit. AutoGenBook 0.3.3 tento klíč nepoužívá a ve vlastních reportech žádné API klíče nezapisuje.

## Distribuční rozsah

Projektový archiv obsahuje celý zdrojový a build projekt, nikoli kryptograficky podepsaný Windows EXE. Podepsaný platformní balík vyžaduje Windows build runner a certifikát vlastníka projektu. Aktualizační `Install-AutoGenBook.cmd` je připraven pro existující lokální instalaci AutoGenBook/Open WebUI a zachovává její datový adresář.
