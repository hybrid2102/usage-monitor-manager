# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate qui.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/)
e il progetto adotta il [Versionamento Semantico](https://semver.org/lang/it/).
Ogni versione corrisponde a una fase del piano di sviluppo (`PLAN.md`, §7).

## [Non rilasciato]

## [0.6.0] - 2026-09-18

Fase 5 - editor delle impostazioni. **Da qui `usage-monitor-config-editor` è
sostituibile**: l'editor del manager conosce tutte le chiavi documentate dei
tre monitor, non solo le tre che il vecchio editor gestiva, e aggiunge
validazione, storico, diff e copia fra istanze.

### Aggiunto

- **`settings/schema.py`** - schema dichiarativo per kind, trascritto dai tre
  `docs/configuration.md`: per ogni chiave `type`, `default`, `section`,
  `help` in italiano, `applies_to`, minimo, scelte e suggerimenti. Tipi:
  `int`, `bool`, `percent`, `hex_color`, `rgba_dict`, `string`, `enum`,
  `string_list`, `wildcard_list`, `command`, `threshold_list`, `amount_list`,
  `cli_command_map`, `icon_fields` e la famiglia `prefix_family` per
  `alert_thresholds_<campo>`.
- **`settings/store.py`** - lettura `utf-8-sig`, `validate()` come porting di
  `settings._validate()` dei monitor (stesse regole, stessi messaggi, stesse
  normalizzazioni: soglie ordinate e deduplicate, comando stringa -> lista,
  `*` al massimo una volta, canali RGBA non validi rimossi), salvataggio
  atomico con `.bak` e **chiavi sconosciute preservate**, `create_default`,
  `suggested_path` (primo elemento della search order del monitor) e
  `copy_sections` per copiare intere sezioni fra istanze.
- **`history.py`** (sezione 9.6) - ogni salvataggio archivia una copia datata
  in `%LOCALAPPDATA%\UsageMonitorManager\history\<istanza>\`, con
  `list_versions`, `read_version`, `diff` per chiave, `restore` (che ripassa
  da `store.save`, quindi si archivia a sua volta), `export` / `import_` di
  profili portabili e potatura alle ultime 50 versioni.
- **`settings/presets.py`** - preset brand e `palette_from_primary` portati dal
  config editor (fusione al 65 % verso bianco/nero per `icon_light` /
  `icon_dark`, gli altri canali restano intatti), più `readable_text_color`.
- **Pagina «Impostazioni»** nella finestra: modulo generato dallo schema con
  sezioni collassabili, ricerca, stato "impostata" e reset al default per
  chiave, validazione inline con le stesse regole del monitor; tab JSON
  editabile con diff "salvato / bozza"; sezione "Altre chiavi" per le chiavi
  sconosciute; tab "Storico" con diff, ripristino, esportazione e importazione;
  barra con *Salva*, *Salva e riavvia istanza*, *Ricarica*, *Crea file*,
  *Apri cartella*, *Copia da...*, preset brand con anteprima e *Testa quick
  action* (con conferma). La voce «Impostazioni…» della dashboard e del menu
  tray apre questa pagina.
- **CLI**: `umm config show|validate|set|unset|create|export|import|history|diff [N]|restore N --instance ID`,
  con `--json` dove ha senso; `set` interpreta il valore come JSON e ricade
  sulla stringa.

### Modificato

- `JsApi` espone `get_settings`, `get_schema`, `validate_draft`,
  `save_settings`, `create_settings`, `list_history`, `get_history_version`,
  `restore_history`, `export_settings`, `import_settings`,
  `copy_settings_from`, `apply_preset`, `get_presets` e `test_quick_action`;
  `get_settings_raw` (sola lettura, Fase 4) è stato sostituito.

## [0.5.0] - 2026-09-18

Fase 4 — tray, dashboard e distribuzione.

### Aggiunto

- **Tray (`ui/tray.py`)**: icona pystray con menu dinamico ricostruito a ogni
  apertura — sottomenu per istanza (Riavvia / Ferma / Avvia, Impostazioni…,
  Apri cartella, Aggiorna… quando c'è una nuova versione), azioni cumulative
  (Riavvia tutte / Ferma tutte / Avvia mancanti), «Controlla aggiornamenti
  (ultimo: HH:MM)», «Apri Manager…» ed «Esci». Icona disegnata con Pillow, con
  badge del numero di aggiornamenti o dell'errore dell'ultimo controllo.
  Notifiche di Windows tramite `Icon.notify`. Le operazioni lunghe girano su
  thread di lavoro: il menu non si blocca mai.
- **Finestra (`ui/window.py` + `ui/web/`)**: pywebview con UI HTML/CSS/JS
  vanilla in tema scuro coerente con i popup dei monitor (`#1e1e1e`,
  `#cccccc`, `#4a9eff`). Tre pagine: Dashboard (tabella §6.6 con azioni di
  riga e toggle di autostart), Aggiornamenti (changelog markdown reso da un
  renderer inline minimo, barra di progresso del download, log degli eventi,
  «Aggiorna» e «Rollback») e Manager (versione, preferenze, presenza del token
  GitHub, stato del rate limit, importazione legacy, log). La finestra viene
  creata una sola volta e nascosta alla chiusura.
- **`manager_config.py`**: stato persistente in
  `%LOCALAPPDATA%\UsageMonitorManager\manager-config.json` con salvataggio
  atomico e backup `.bak`; istanze dichiarate ed etichettate dall'utente,
  preferenze, `last_check` e `skipped_versions`.
- **`scheduler.py`**: controllo aggiornamenti periodico (primo controllo dopo
  30 s, poi ogni `check_interval_hours`, default 6 h) con stato in memoria,
  notifica una sola volta per release e rispetto di `skipped_versions`.
- **`legacy.py`**: importazione guidata della configurazione precedente
  (shortcut `<exe> - *.lnk`, voci `HKCU\...\Run`, `profiles.json` del vecchio
  config editor) con etichette suggerite; eseguita una volta al primo avvio e
  disponibile come `umm import-legacy [--dry-run]` e dalla pagina Manager.
- **Autostart**: toggle dell'avvio con Windows per il manager (valore
  `UsageMonitorManager`, con risincronizzazione del percorso a ogni avvio) e
  per ogni istanza (`run_key_base` + suffisso, con lo stesso comando che
  scriverebbe il monitor).
- **CLI**: `umm tray`, `umm import-legacy`, `umm autostart --manager on|off|status`
  e `umm autostart --instance ID on|off|status`.
- **Build e release**: `build.py`, `usage_monitor_manager.spec` (PyInstaller
  onefile, `--noconsole`, asset web inclusi, `version_info.py` generato da
  `__version__`), icona generata da `tools/generate_icon.py` e workflow di
  release che su tag `v*` pubblica `UsageMonitorManager.exe` e il relativo
  `.sha256`.

### Modificato

- `discovery.discover()` unisce ora anche le istanze dichiarate in
  `manager-config.json` (sorgente «d» del §6.1) ed è da lì che arrivano le
  etichette utente.
- I lettori/scrittori della Run key accettano una radice di registro
  iniettabile, così i test non toccano mai `HKCU\...\Run`.

## [0.4.0] - 2026-09-17

Fase 3 — motore di aggiornamento.

### Aggiunto

- `github.py`: API delle release con cache ETag, gestione del rate limit,
  selezione dell'asset per architettura e download con progresso.
- `integrity.py`: sha256 dal digest dell'API o dal file `.sha256` affiancato,
  stato Authenticode a titolo informativo.
- `updater.py`: da `UpdatePlan` all'esecuzione — stop delle istanze, backup,
  swap del file con ritenta (tolleranza ai lock di OneDrive), riavvio e
  rollback automatico in caso di errore.
- `umm check`, `umm update` e `umm rollback`, con changelog in console.

## [0.3.0] - 2026-09-17

Fase 2 — controllo dei processi.

### Aggiunto

- `process_control.py`: stop / start / restart per istanza e cumulativi, con
  attesa del rilascio del mutex, verifica dell'eseguibile prima di ogni
  terminazione e ambiente ripulito per i figli.
- `umm restart`, `umm stop`, `umm start` con selettori e `--dry-run`.

## [0.2.0] - 2026-09-17

Fase 1 — discovery e versioni.

### Aggiunto

- `discovery.py`: scansione dei processi, collasso delle coppie PyInstaller,
  risoluzione della config dir (riga di comando, ambiente, default), ordine di
  ricerca dei settings e istanze dormienti da Run key, shortcut e file di
  impostazioni.
- `versions.py` e `platform/win32.py`: lettura della `ProductVersion` dal PE,
  confronto di versioni, lettura della Run key e risoluzione degli shortcut.
- `umm list` (con `--json`).

## [0.1.0] - 2026-09-17

Fase 0 — scheletro.

### Aggiunto

- Layout `src/`, `pyproject.toml`, ruff / pyright (strict) / pytest /
  pre-commit, CI su `windows-latest`, registro dei tipi di monitor
  (`kinds.py`), modello di dominio (`model.py`) e README in italiano.

[Non rilasciato]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/hybrid2102/usage-monitor-manager/releases/tag/v0.1.0
