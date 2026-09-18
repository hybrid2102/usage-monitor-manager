# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate qui.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/)
e il progetto adotta il [Versionamento Semantico](https://semver.org/lang/it/).
Ogni versione corrisponde a una fase del piano di sviluppo (`PLAN.md`, §7).

## [Non rilasciato]

## [1.0.0] - 2026-09-18

Prima versione stabile. Consolida le fasi 0-8 del piano
([PLAN.md](PLAN.md) §7) e **sostituisce i due strumenti precedenti**,
`usage-monitor-updater` (PowerShell) e `usage-monitor-config-editor` (PySide6).

Le voci di dettaglio delle singole fasi restano sotto, da 0.8.0 a 0.1.0.

### Che cos'e'

Una tray app e una CLI (`umm`) che gestiscono la famiglia **Usage Monitor** -
Claude (due istanze), Copilot, Codex - piu' qualsiasi eseguibile portabile
aggiornato da GitHub Releases, `AgentMonitorForClaude.exe` incluso.

### Contenuto

- **Discovery** (fase 1): scan dei processi con collasso delle coppie
  bootloader/app di PyInstaller, config dir da `--config-dir` -> env var ->
  default con la sorgente registrata, suffisso di istanza identico a quello dei
  monitor, replica della search order dei settings con i file "in ombra",
  istanze dormienti da Run key, shortcut legacy, file di settings sparsi,
  dichiarazioni dell'utente ed exe scan. `umm list [--json]`.
- **Controllo processi** (fase 2): `stop` (app poi bootloader, attesa del
  rilascio del mutex), `start` (staccato, senza finestra, `--config-dir`
  sempre esplicito, ambiente ripulito dalle variabili `_PYI_*`), `restart` con
  due secondi fra due istanze dello stesso exe.
- **Aggiornamenti** (fase 3): un piano **per eseguibile**, changelog prima del
  download, sha256 obbligatorio salvo conferma esplicita, swap con `.bak`,
  riavvio delle sole istanze che erano attive, verifica post-avvio e rollback.
- **Tray e finestra** (fase 4): menu dinamico con badge e notifiche, dashboard,
  pagina aggiornamenti con changelog e progresso, import della configurazione
  precedente al primo avvio, autostart del manager e delle istanze.
- **Editor delle impostazioni** (fase 5): schema delle chiavi documentate dei
  tre monitor, validazione con le regole del monitor **prima** di scrivere,
  chiavi sconosciute conservate, storico datato con diff e ripristino,
  export/import, copia fra istanze, preset brand. `umm config ...`.
- **Self-update e robustezza** (fase 6): `umm self-update` (rename-swap e
  rilancio con `--post-update`), token nel Windows Credential Manager,
  `.umm-archive` con keep-N e ripristino di una qualsiasi build, canale
  prerelease e "salta versione", watchdog (crash, duplicati, coppie orfane),
  `umm update --all --yes --log` per il Task Scheduler.
- **Estensioni** (fase 7): kind generico "exe da GitHub Releases" con
  `umm kind add`, modalita' diagnostica (`--verbose` su file e viewer),
  `status.json` per statusline e script, profili di avvio, hotkey globale.
- **Rifinitura** (fase 8): interfaccia e CLI in **italiano e inglese**
  (`preferences.language`, `auto` segue il locale di sistema), gerarchia di
  errori unica con i codici di uscita documentati e i traceback solo con
  `--debug`, `manager.log` rotante, guida utente e documento di architettura,
  stub Linux documentato.

### Sicurezza

Le invarianti sono elencate in [docs/architecture.md](docs/architecture.md): si
termina solo un PID il cui eseguibile coincide, "fermata" significa mutex
rilasciato, l'ambiente del figlio e' sempre ripulito, ogni scrittura di un file
dell'utente e' atomica e lascia un `.bak`, il token non passa mai da `argv`.

### Requisiti

Windows 10/11, Python 3.12+ per l'uso da sorgente; l'eseguibile congelato non
richiede nulla.

## [0.8.0] - 2026-09-18

Fase 7 - estensioni. Il manager non gestisce piu' solo i tre monitor: sa
descrivere qualsiasi eseguibile portabile aggiornato da GitHub Releases, sa
avviare un'istanza in modalita' diagnostica, pubblica il proprio stato su file,
applica insiemi di istanze con un clic e si apre con una combinazione di tasti.

### Aggiunto

- **Kind generico «exe da GitHub Releases»** (sezione 9.9) - il registro dei
  tipi ha ora due famiglie. `usage-monitor` e' un membro della famiglia dei
  monitor (config dir, env var, suffisso di istanza, file di settings);
  `github-exe` e' il caso generale: un eseguibile portabile senza config dir,
  senza variabile d'ambiente e senza settings, una sola istanza per percorso.
  Il built-in `agent-monitor-claude` (`AgentMonitorForClaude.exe`, stesso
  autore e stessa cartella dei monitor) e' il primo caso. Gli altri si
  dichiarano in `custom_kinds` di `manager-config.json` - a mano, con
  `umm kind add`, o dal form della pagina Manager - e vengono uniti ai built-in
  all'avvio: un kind definito dall'utente e' a tutti gli effetti parte della
  discovery, del motore di update, del watchdog e della UI. Una voce non valida
  viene **saltata e spiegata**, non fa cadere il registro. Un `github-exe` non
  ha il proprio ambiente letto (niente `environ()`: sarebbe il rischio della
  sezione 8 senza alcun guadagno) e viene trovato, quando e' fermo, solo nelle
  cartelle in cui la discovery ha gia' identificato un eseguibile - mai con una
  scansione del disco. CLI: `umm kind list|add|remove`.
- **Modalita' diagnostica** (sezione 9.7) - `diag.py` riavvia un'istanza con
  `--verbose` e cattura stdout e stderr su
  `%LOCALAPPDATA%\UsageMonitorManager\logs\<istanza>\<data>.log`; `stop` la
  riporta in modalita' normale. Quale istanza sia verbosa e' registrato in
  `diag.json`, perche' il flag vive nel processo e non in un file che il monitor
  legge: senza quella nota un manager riavviato non potrebbe dirlo. Uno stop
  fallito non lancia nulla, come in `process_control.restart`. Il bottone
  «Copia diagnostica» produce il blocco da incollare in una issue upstream
  (versioni, path, ultime righe di log, `umm status --json` ridotto: nessun
  dump dell'ambiente, nessun token). CLI: `umm diag start|stop|show`, con
  `--dry-run`.
- **`manager.log`** - file rotante 1 MB x 3 in `logs\`, a cui scrivono tray,
  finestra, scheduler, watchdog e updater; la pagina Manager ne mostra la coda.
  Un logging che non riesce a scrivere (`%LOCALAPPDATA%` in sola lettura)
  lascia il logger senza handler e non impedisce l'avvio.
- **File di stato `status.json`** (sezione 9.11) - `status_file.py` scrive, in
  modo **atomico** (file temporaneo + rename), lo stesso documento di
  `umm status --json`, dove indica la preferenza `status_file`; la tray lo
  riscrive dopo ogni ciclo. Serve a statusline e script: nessun processo Python
  da avviare, nessuna chiamata di rete, e `generated_at` per accorgersi che il
  manager non gira piu'. Schema ed esempio di consumo in
  [`docs/status-file.md`](docs/status-file.md). Nuovo
  `umm status --write-file [PERCORSO]`.
- **Profili di avvio** (sezione 9.13) - `profiles.py`: un nome e un elenco di
  id di istanza in `manager-config.json`. Applicare un profilo avvia i membri
  che non girano e ferma i **non** membri - ma solo quelli della famiglia
  `usage-monitor`: un `github-exe` fuori dal profilo resta in esecuzione e
  viene segnalato, perche' terminare in silenzio un agent monitor e' esattamente
  la sorpresa che questo manager esiste per evitare. `plan` calcola tutto senza
  toccare niente (ed e' cio' che stampa `--dry-run`), `apply_plan` e' l'unica
  funzione che ferma o avvia, con lo stesso intervallo di due secondi fra due
  start. CLI `umm profile list|add|remove|show|apply` (`--dry-run`, `--yes`,
  `--no-stop-others`) e submenu «Profili» nella tray.
- **Hotkey globale** (sezione 9.14) - `RegisterHotKey` in ctypes puro, senza
  `pynput`: la preferenza `hotkey` (es. `ctrl+alt+u`) apre o nasconde la
  finestra del manager. Registrazione, loop `GetMessageW` e deregistrazione
  stanno sullo stesso thread, come Windows richiede; fermare significa postare
  `WM_QUIT`, non alzare un flag. Almeno un modificatore e' obbligatorio
  (Windows registrerebbe un tasto nudo sequestrandolo a tutto il sistema) e una
  combinazione occupata da un'altra applicazione viene riportata in chiaro
  accanto al campo. I punti di ingresso Windows sono iniettabili, cosi' i test
  coprono tutto il ciclo di vita senza sequestrare nulla sulla macchina.

### Documentazione

- `docs/status-file.md` - schema del documento, come si attiva, esempio di
  statusline in Node e in PowerShell.

## [0.7.0] - 2026-09-18

Fase 6 - self-update e robustezza. Il manager sa aggiornare se stesso, conserva
le build precedenti, sorveglia le istanze e ha una modalita' completamente non
interattiva per il Task Scheduler.

### Aggiunto

- **`secrets.py`** (sezione 6.5-bis) - token GitHub nel **Windows Credential
  Manager** via ctypes `advapi32` (`CredReadW` / `CredWriteW` / `CredDeleteW`,
  `CRED_TYPE_GENERIC`, target `UsageMonitorManager/github`, persistenza
  `CRED_PERSIST_LOCAL_MACHINE`), nessuna dipendenza `keyring`. Ordine di
  risoluzione Credential Manager -> `GITHUB_TOKEN` -> `GH_TOKEN` -> anonimo;
  `github.resolve_token` delega qui. Il valore non passa mai da `argv` e non
  viene mai ristampato: `umm token set` lo legge da stdin o da `--from-env VAR`,
  la UI usa un campo mascherato e mostra solo "presente / assente".
- **`self_update.py`** (sezione 6.5) - `check_self()` sul repo del manager
  (nessuna release pubblicata = stato `unknown` con "nessuna release
  pubblicata", non un errore) e `apply_self()` solo sulla build congelata:
  download `.download` accanto a `sys.executable`, verifica sha256, `exe` ->
  `exe.bak`, `.download` -> `exe`, rilancio `[nuovo exe, --post-update, <pid>]`
  staccato e con ambiente ripulito dalle variabili `_PYI_*` / `_MEI*`, poi
  uscita pulita della tray. `umm --post-update PID` attende la morte del
  processo precedente (psutil, 30 s), cancella il `.bak`, acquisisce il mutex
  singleton con attesa e avvia la tray notificando "Manager aggiornato alla
  vX.Y.Z". Da sorgente il self-update si rifiuta con un messaggio chiaro.
- **`archive.py`** (sezione 9.4) - a ogni aggiornamento riuscito l'eseguibile
  sostituito viene **spostato** in `<cartella exe>\.umm-archive\` come
  `<Nome>-<versione>.exe`, con un `manifest.json` che registra versione,
  sha256, data e tag di release; si conservano le ultime N build per
  eseguibile (`preferences.archive_keep`, default 3). `restore()` ferma le
  istanze, rimette al suo posto la build scelta - archiviando a sua volta
  quella corrente, quindi il ripristino e' reversibile - e riavvia.
  CLI: `umm rollback --kind K [--version V]`, `umm archive list|prune`.
- **`watchdog.py`** (sezione 9.2) - thread che confronta snapshot consecutivi
  ogni 30 s e segnala `crashed` (era in esecuzione, non lo e' piu'),
  `duplicate` (due processi app per lo stesso suffisso) e `orphan` (coppia
  bootloader/app incompleta). Le operazioni del manager (stop, riavvio,
  aggiornamento) si annunciano in un registro "expected down" a scadenza, cosi'
  non vengono scambiate per crash. Riparazioni: avvio, kill dei duplicati
  tenendo il **piu' vecchio** (e solo processi il cui exe coincide), riavvio
  della coppia orfana. `preferences.auto_restart_crashed` (default: off)
  riavvia da solo.
- **Canale prerelease e "salta versione"** (sezione 9.5) - `include_prerelease`
  globale piu' override per repo (`prerelease_repos`), `skipped_versions[repo]`
  scritto da tray, UI e `umm skip --kind K` (anche `--kind manager`);
  `umm check` mostra lo stato `skipped` e non esce piu' 10,
  `--include-skipped` forza. `umm unskip --kind K` annulla.
- **Aggiornamento pianificato** (sezione 9.10) - `umm update --all --yes` non
  chiede mai nulla: un asset senza sha256 viene **saltato** con uscita 4
  (`--allow-unverified` per accettarlo), un errore di rete esce 3, un
  fallimento parziale esce 1. `--log FILE` scrive una riga JSON per ogni passo.
  Il README documenta un esempio con `schtasks`.
- **`umm status [--json] [--check]`** (sezione 9.11, forma minima) - istanze,
  stato aggiornamenti memorizzato e una passata di watchdog; **nessuna chiamata
  di rete** senza `--check`. Il file `status.json` resta alla fase 7.
- **UI** - pagina Manager con token (imposta / rimuovi / stato / rate limit) e
  self-update con progresso; pagina Aggiornamenti con le versioni archiviate e
  "Ripristina" piu' "Salta questa versione"; dashboard con colonna Stato,
  badge delle anomalie e bottone "Ripara"; tray con "Aggiorna il manager",
  "Salta questa versione", "Ripara", tooltip e badge che contano le anomalie.
- **`discovery.running_processes()`** - vista *non collassata* dei processi in
  esecuzione: `discover()` unisce tutto in una istanza per `(kind, config dir)`,
  che e' esattamente cio' che nasconderebbe un duplicato al watchdog.

### Modificato

- `umm update --yes` **non** installa piu' un asset privo di sha256: era la
  scorciatoia interattiva della fase 3, ora e' il percorso non presidiato e
  rifiuta, uscendo 4. `--allow-unverified` e' l'opt-in esplicito.
- `updater.apply` archivia la build precedente prima di cancellare il `.bak` e
  annota nel manifest la release appena installata; `plan_updates` accetta
  `prerelease_for(repo)`.
- `UpdateState` espone `skipped` e `self_update`, e `update_count` conta anche
  l'aggiornamento del manager.

### Test

623 test. `tests/conftest.py` vieta a tutta la suite la rete (`github._open`) e
il Credential Manager reale; i test del Credential Manager vero usano il target
`UsageMonitorManagerTest/github` e lo cancellano.

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

[Non rilasciato]: https://github.com/hybrid2102/usage-monitor-manager/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.8.0...v1.0.0
[0.8.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/hybrid2102/usage-monitor-manager/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/hybrid2102/usage-monitor-manager/releases/tag/v0.1.0
