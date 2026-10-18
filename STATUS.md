# Stato di avanzamento

Aggiornato: 2026-10-18. Riferimento: [PLAN.md](PLAN.md) §7.

## Versione **1.1.2**

[v1.1.2](https://github.com/hybrid2102/usage-monitor-manager/releases/tag/v1.1.2)
su GitHub Releases, con `UsageMonitorManager.exe` e `.sha256`.

L'editor permette di creare settings indipendenti per le istanze con cartella
personalizzata da **Altro → Crea settings indipendenti**, includendo la bozza
non ancora salvata senza modificare il file condiviso. Riavviare poi l'istanza.
La pagina Aggiornamenti mostra anche il manager con le due versioni e un
collegamento alla pagina dedicata; conteggio e badge includono il manager.
I preset usano colori più vivaci per testo e barrette della tray, preservando
trasparenze e avvisi. Riapplicare il preset e salvare/riavviare l'istanza.

| Fase | Contenuto | Versione |
|---|---|---|
| 0 | Layout, kind registry, modello, tooling, CI | 0.1.0 |
| 1 | Discovery, versioni PE, `umm list` | 0.2.0 |
| 2 | Start/stop/restart, pulizia ambiente figli | 0.3.0 |
| 3 | Update engine, `umm check|update|rollback` | 0.4.0 |
| 4 | Tray, finestra, scheduler, import legacy, autostart, build/release | 0.5.0 |
| 5 | Editor impostazioni, storico/diff, preset, `umm config` | 0.6.0 |
| 6 | Self-update, token, archivio/rollback, prerelease/skip, watchdog | 0.7.0 |
| 7 | Kind `github-exe`, diagnostica, `status.json`, profili, hotkey | 0.8.0 |
| 8 | i18n it/en, `errors.py`, documentazione, stub Linux, release 1.0 | 1.0.0 |

Gate all'ultimo commit: `ruff check`, `ruff format --check`, `pyright` strict,
**915 test** verdi.

## Fase 7 — commit

Codice `27928fa` (wip), test `2107b0f`, `6d42d36`, `8e6f965`, `28a97ed`,
documentazione e versione 0.8.0 `d927115`.

## Fase 8 — commit

| Passo | Contenuto | Commit |
|---|---|---|
| B1a | `i18n.py`, cataloghi `locale/it.json` + `en.json`, `locale` nei datas dello spec, `set_language` da CLI e tray, `JsApi.get_translations()`, stringhe della tray | `a520dd9` |
| B1b | 28 messaggi di `JsApi`, 93 etichette e 6 placeholder di `index.html` (`data-i18n`), `applyTranslations()` in `app.js` | `99fb97e` |
| B1c | ~70 stringhe dinamiche di `app.js` | `f8654e3` |
| B1d | 61 `help=` e ~90 messaggi della CLI, fixture `english_cli` nei moduli di test della CLI | `c7eee0f` |
| B2 | `errors.py`, codici di uscita, `--debug`, traceback su `manager.log`, `@errors.guarded` | `220e72b` |
| B3/B4 | `docs/architecture.md`, stub Linux verificato con test, README ridotto (489 -> ~120 righe) | `ea702b6` |
| B5 | versione 1.0.0, CHANGELOG consolidato, build verificata | `61e235b` |
| B6 | finestra ridisegnata (sidebar, schede istanza, toast, tema, pannello Attività), inclusa nel changelog di 1.0.0 | `c17a2b4` |
| B7 | fix `attach_parent_console` (stdout non-`None` ma non scrivibile: `--version` non stampava nulla su build `--noconsole`), scoperto dallo smoke test della release | `7a4dbbb` |
| B8 | smoke test della CI passato a `Start-Process -RedirectStandardOutput` (la cattura `(& $exe) -join ""` di PowerShell non dà uno stdout scrivibile a un exe `--noconsole`) | `97de391` |

Catalogo: **421 chiavi** in entrambe le lingue, nessuna inutilizzata; i test
statici verificano parita' delle chiavi, parita' dei segnaposto ed esistenza di
ogni chiave usata nei `.py`, nei `.js` e negli attributi `data-i18n`.

Build verificata in questo worktree:

- `.venv\Scripts\python build.py --clean` con `__version__` 0.8.0 ->
  `dist\UsageMonitorManager.exe --version` = **0.8.0**;
- bump a 1.0.0, `.venv\Scripts\python build.py --clean --sha` ->
  `--version` = **1.0.0**, PE `ProductVersion` = `FileVersion` = **1.0.0**,
  `ProductName` = `Usage Monitor Manager`, 19.7 MiB, sha256 accanto all'exe;
- l'exe congelato carica i cataloghi dal bundle (`--help` in italiano,
  `kind list` elenca le due famiglie).

## Cosa resta all'utente

1. **Prova manuale della tray e della finestra.** Le fasi 5-8 sono verificate a
   livello API e di test, non cliccando: menu, notifiche, le cinque pagine,
   editor delle impostazioni, profili, diagnostica, hotkey.
2. **Archiviazione dei due repo vecchi**, ora che la 1.0 e' pubblicata: i
   README di rimando sono preparati in `docs/deprecation/` (scritti in un
   worktree separato). Applicarli e poi archiviare:

   ```powershell
   gh repo edit hybrid2102/usage-monitor-updater --archived
   gh repo edit hybrid2102/usage-monitor-config-editor --archived
   ```

3. **Preferenze da decidere al primo avvio reale**: lingua (`it` per default,
   `auto` segue il locale di sistema), autostart del manager, `status_file` se
   si vuole alimentare la statusline, `hotkey`.

## Regole per chi riprende

- Ogni brief a un subagente deve vietare: stop/start/restart/update delle
  istanze reali, scrittura dei settings reali, della chiave Run reale e di
  token reali; solo `--dry-run` e comandi read-only sul reale; test con
  discovery, process control e rete finti.
- Motivo: nella fase 2 un agente ha riavviato le istanze reali ereditando
  `CLAUDE_CONFIG_DIR` e variabili `_PYI_*` dalla shell della sessione.
