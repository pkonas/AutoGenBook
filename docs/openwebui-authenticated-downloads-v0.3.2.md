# AutoGenBook pro Open WebUI — instalační a aktualizační projekt 0.3.2

Tento projekt integruje AutoGenBook do desktopové instance Open WebUI bez forku Open WebUI. Verze 0.3.2 opravuje stahování vygenerovaných výstupů z odpovědí AutoGenBooku.

## Co verze 0.3.2 opravuje

Ve verzi 0.3.1 existovaly dvě samostatné chyby:

1. Textové odkazy směřovaly na chráněný endpoint `/api/v1/files/<id>/content`. Odkaz otevřený v novém panelu neposílá bearer token uložený SPA klientem, a proto Open WebUI odpovědělo `{"detail":"Not authenticated"}`.
2. Souborová karta otevřela standardní modal Open WebUI. Vygenerovaný binární soubor byl zaregistrován s `process=false` a jeho databázový záznam neměl extrahovaný text v `data.content`; modal proto zobrazil `No content`.

Verze 0.3.2:

- ponechává výstup jako běžný soubor vlastněný uživatelem Open WebUI;
- přidává stabilní download endpoint na stejné Open WebUI origin;
- používá HMAC capability omezenou na jedno ID souboru a jednoho vlastníka;
- nevkládá do URL Open WebUI API klíč, session token ani Companion bearer token;
- podporuje `GET`, `HEAD`, `Range`, `206 Partial Content`, `416`, `ETag` a blokové streamování;
- ukládá do `data.content` smysluplnou informační stránku s funkčním tlačítkem ke stažení;
- přesouvá download route před SPA catch-all, takže ji frontend nepohltí;
- znovu používá již registrované soubory a opraví jejich chybějící obsah při příkazu `výstupy JOB_ID`.

## Požadavky

- existující AutoGenBook Open WebUI Function verze 0.3.1;
- Open WebUI Desktop nebo server, ke kterému se lze připojit z počítače s instalátorem;
- Python 3.10 nebo novější;
- osobní **administrátorský** API klíč Open WebUI uložený v TXT souboru;
- oprávnění uživatele ke správě Functions.

API klíč se předává pouze jako odkaz na TXT soubor. Není zapisován do projektu, reportu ani výsledné Function.

## Doporučená instalace na Windows

1. Rozbalte celý projekt do samostatného adresáře. Nespouštějte CMD přímo uvnitř ZIPu.
2. V Open WebUI vytvořte administrátorský API klíč.
3. Uložte jej jako jediný řádek například do:

   ```text
   C:\Users\petr\.secrets\openwebui-admin-api-key.txt
   ```

4. Spusťte:

   ```cmd
   Install-AutoGenBook.cmd ^
     --openwebui-url "http://127.0.0.1:8080" ^
     --webui-api-key-file "C:\Users\petr\.secrets\openwebui-admin-api-key.txt"
   ```

   Bez parametrů instalátor vyžádá obě hodnoty interaktivně.

5. Restartujte Open WebUI Desktop. Restart je důležitý, protože odstraní starou instanci Function z cache a znovu sestaví pořadí HTTP routes.
6. Ve Function zkontrolujte frontmatter `version: 0.3.2`.

Instalátor vytvoří:

```text
dist\AutoGenBook-OpenWebUI-Function-v0.3.2.py
dist\AutoGenBook-OpenWebUI-Function-v0.3.1-backup.py
dist\AutoGenBook-OpenWebUI-v0.3.2-UPDATE-REPORT.json
```

## Ruční aktualizace Function

Nemá-li API klíč administrátorské oprávnění, exportujte současný zdroj Function 0.3.1 do souboru a spusťte:

```cmd
Install-AutoGenBook.cmd ^
  --function-file "D:\temp\AutoGenBook-OpenWebUI-Function-v0.3.1.py"
```

Vygenerovaný soubor `dist\AutoGenBook-OpenWebUI-Function-v0.3.2.py` poté vložte do **Admin Panel → Functions → autogenbook_companion** a uložte.

## Obnova výstupů již dokončené úlohy

Staré zprávy obsahují staré odkazy a nelze je zpětně přepsat. Po aktualizaci odešlete v AutoGenBook chatu:

```text
výstupy JOB_ID
```

nebo anglicky:

```text
outputs JOB_ID
```

Příklad:

```text
výstupy c1e7de76-c63b-4915-a748-fc161e6ea2ee
```

AutoGenBook znovu negeneruje dokument ani nevolá LLM. Existující artefakty streamovaně uloží do Open WebUI, opraví metadata a vytvoří novou odpověď s funkčními kartami a odkazy.

## Jak stahování funguje

Výsledný odkaz má podobu:

```text
https://openwebui.example/autogenbook-output/<FILE_ID>/<JMENO>?uid=<USER_ID>&cap=<HMAC>
```

`cap` je neodhadnutelný HMAC capability token. Je svázán s jediným `FILE_ID` a vlastníkem. Server před odesláním ověří:

- platnost capability;
- existenci souborového záznamu;
- shodu vlastníka;
- existenci fyzických dat v úložišti Open WebUI.

Odkaz nemá časovou expiraci. Přestane fungovat, pokud je soubor odstraněn, uživatel je zrušen nebo dojde k vědomé rotaci souboru se secret key `openwebui-output-download.key`.

Capability URL je podobně citlivá jako neveřejný odkaz ke sdílenému souboru: kdo získá celý odkaz, může daný soubor stáhnout. Nezveřejňujte jej mimo zamýšlené příjemce.

## Velké soubory

Download route podporuje pokračování přerušeného přenosu:

```text
Range: bytes=104857600-
```

Vrací:

```text
206 Partial Content
Accept-Ranges: bytes
Content-Range: bytes START-END/TOTAL
ETag: "..."
```

Soubor se čte po blocích, nikoli celý do RAM.

## Diagnostika

### `Not authenticated`

Po aktualizaci se nové odkazy nesmějí skládat z `/api/v1/files/.../content`. Vygenerujte novou odpověď příkazem `výstupy JOB_ID` a ověřte, že odkaz obsahuje `/autogenbook-output/`.

### `No content`

Po příkazu `výstupy JOB_ID` musí modal zobrazit informační text a odkaz ke stažení. Pokud stále zobrazuje `No content`, běží stará Function z cache nebo nebyla aktualizována databázová položka. Restartujte Open WebUI a příkaz zopakujte.

### Route vrací HTML aplikace

To znamená, že route byla zaregistrována za SPA catch-all. Verze 0.3.2 ji explicitně přesouvá před route `spa-static-files`; restartujte Open WebUI po aktualizaci.

### API odpoví 401/403 při instalaci

TXT soubor neobsahuje platný administrátorský API klíč. Browserová session/JWT není vhodná. Vytvořte nový klíč v Open WebUI a aktualizujte pouze obsah TXT souboru.

## Bezpečnostní vlastnosti

- žádné API klíče v URL;
- žádný Companion bearer token v URL;
- capability používá HMAC-SHA-256 a konstantní porovnání;
- kontrola vlastníka souboru na serveru;
- název souboru se bere z databáze, nikoli z URL;
- `Content-Disposition` používá bezpečně zakódovaný UTF-8 název;
- `X-Content-Type-Options: nosniff`;
- download secret má být uložen s právy pouze pro aktuálního uživatele;
- Range požadavky jsou omezeny na jediný rozsah;
- path traversal není možný, protože klient neposílá cestu na disku.

## Vývoj a ověření

Hlavní soubory opravy:

```text
tools/apply_openwebui_authenticated_downloads_v032.py
tools/install_authenticated_downloads_v032.py
Install-AutoGenBook.cmd
```

Kontrola syntaxe:

```bash
python -m py_compile tools/apply_openwebui_authenticated_downloads_v032.py
python -m py_compile tools/install_authenticated_downloads_v032.py
python tools/install_authenticated_downloads_v032.py --self-test
```

Sestavení celého předávacího projektu:

```bash
python tools/build_openwebui_authenticated_downloads_project_v032.py
```

## Rozsah balíku

Výsledný ZIP obsahuje celý zdrojový projekt AutoGenBooku, integrační a instalační zdroje, testovací workflow, updater 0.3.2, dokumentaci, manifesty a build provenance. Nejde o kryptograficky podepsaný Windows EXE ani notarizovaný macOS balík.
