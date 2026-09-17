# usage-monitor-manager — Piano di sviluppo

> Stato: **approvato nelle decisioni di fondo** (2026-09-17, vedi §10), esecuzione non ancora iniziata. Nessuna riga di codice di questo piano è ancora stata scritta.
> Il documento è diviso in: ricognizione (cosa ho imparato dalla famiglia di repo e dalla macchina), decisioni architetturali, design dei moduli, fasi di lavoro, rischi, proposte extra e decisioni che spettano a te.

---

## 1. Ricognizione della famiglia "Usage Monitor"

Lista GitHub: https://github.com/stars/hybrid2102/lists/usage-monitor-family (7 repo). Ho clonato e letto sorgenti, docs, workflow e release di ciascuno.

### 1.1 `jens-duttke/usage-monitor-for-claude` (capostipite, v1.22.0)

Tray app Python (pystray + pywebview/WebView2), EXE singolo PyInstaller `UsageMonitorForClaude.exe` (~13 MB). Fatti rilevanti per il manager:

| Aspetto | Dettaglio |
|---|---|
| Identità istanza | `--config-dir=PATH` (o env `CLAUDE_CONFIG_DIR`) → `config_dir_suffix()` = `""` per `~/.claude`, altrimenti `_` + sha1(normcase(path))[:12] |
| Singleton | Mutex Win32 `UsageMonitorForClaude_SingleInstance<suffix>` + file mapping `UsageMonitorForClaude_HolderPID<suffix>` (PID + versione in shared memory) |
| Autostart | L'app scrive da sé `HKCU\...\Run\UsageMonitorForClaude<suffix>` con exe + `--config-dir` |
| Settings | `usage-monitor-settings.json`, ricerca: `1) $CONFIG_DIR/` (solo se non default) `2) accanto all'exe` `3) ~/.claude/`. L'app non lo crea mai; validazione in `settings._validate()` con drop delle chiavi invalide + MessageBox |
| Versione | `__version__` + `version_info.py` → PE `ProductVersion` `1.22.0.0` leggibile dal file |
| Restart | Voce "Restart" nel menu tray: l'app rilancia sé stessa (`restart_requested`) passando `--config-dir` e `--verbose` |
| Processi | PyInstaller onefile: **2 processi** per istanza (bootloader padre + app figlio, stesso exe) + figli `msedgewebview2.exe` |
| Release | Asset unico `UsageMonitorForClaude.exe`; **nessun `.sha256` allegato**, ma l'API GitHub espone `assets[].digest` (`sha256:...`); build non firmata |
| Update check | Non integrato: doc `automatic-update-check.md` propone uno script `on_reset_command` che legge `USAGE_MONITOR_VERSION` e mostra un toast |
| Diagnostica | `--verbose` apre una console e stampa diagnostica di avvio/runtime |
| Config keys | ~40 chiavi in 12 sezioni (alert, cli_command, tooltip/popup/icon fields, compact_hide, event commands, polling, language, time_format, currency, icon colors RGBA, popup colors hex). Riferimento: `docs/configuration.md` |

### 1.2 `hybrid2102/usage-monitor-for-claude` (fork)

Fork allineato a monte fino al 2026-09-05, nessuna release propria. **Gli aggiornamenti dell'istanza Claude vanno presi da `jens-duttke`**, non dal fork.

### 1.3 `hybrid2102/usage-monitor-for-copilot` (v0.3.2) e `hybrid2102/usage-monitor-for-codex` (v0.1.0)

Derivati con la stessa architettura, rinominati package per package. Differenze che contano per il manager:

| | Claude | Copilot | Codex |
|---|---|---|---|
| Env var config dir | `CLAUDE_CONFIG_DIR` | `COPILOT_HOME` | `CODEX_HOME` |
| Config dir default | `~/.claude` | `~/.copilot` | `~/.codex` |
| Exe / PE ProductName | `UsageMonitorForClaude.exe` | `UsageMonitorForCopilot.exe` | `UsageMonitorForCodex.exe` |
| Repo release | `jens-duttke/usage-monitor-for-claude` | `hybrid2102/usage-monitor-for-copilot` | `hybrid2102/usage-monitor-for-codex` |
| Asset release | `.exe` (digest via API) | `.exe` + `.exe.sha256` | `.exe` + `.exe.sha256` |
| Campi quota | `five_hour`, `seven_day*` | `chat`, `completions`, `premium_interactions` | `five_hour`, `seven_day` |
| Chiavi settings specifiche | `alert_thresholds_five_hour/seven_day/extra_usage`, `alert_extra_usage_spent`, `notify_claude_update`, `currency_symbol` | `alert_thresholds_<field>` senza fallback di periodo, `notify_copilot_update`, nessuna currency | come Claude ma `notify_codex_update` |
| Sezioni `compact_hide` | `account`, `extra_usage`, `claude_code`, `status` | `copilot_code`, `status` | `codex_code`, `status` |

Tutto il resto (meccanismo `--config-dir`, mutex, ricerca settings, restart, doppio processo, `version_info.py`) è identico: **una sola implementazione parametrizzata per "kind" copre i tre monitor**.

### 1.4 `hybrid2102/usage-monitor-updater` (PowerShell, v1.1.1) — da sostituire

Idee da riportare in Python:

- Confronto versione **PE `ProductVersion`** vs tag release (normalizzati a 3 componenti).
- Changelog stampato **mentre** scarica; link markdown resi come testo.
- Stop solo dei processi con **quel path** di exe (non per nome).
- Download **nella stessa cartella** del target (`.download`), così lo swap finale è un rename atomico; `.bak` mantenuto finché lo swap non riesce, ripristino su errore.
- Verifica: SHA-256 dal `digest` GitHub, poi Authenticode (unsigned accettato se il digest combacia).
- Retry sul rename (antivirus tiene il lock qualche secondo).
- Istanze aggiuntive = shortcut `<exe> - *.lnk` accanto all'exe, riavviate 2 s dopo la principale.
- Codici di uscita distinti, modalità `-NonInteractive` per Task Scheduler, gestione rate-limit API (60 req/h anonimi).

### 1.5 `hybrid2102/usage-monitor-config-editor` (PySide6 + Nuitka, v0.2.0, EXE 89 MB) — da sostituire

Idee da riportare:

- Registro profili persistente (`%LOCALAPPDATA%\UsageMonitorConfigEditor\profiles.json`).
- Discovery dei `usage-monitor-settings.json` in `~`, `%LOCALAPPDATA%`, `%APPDATA%` e nelle loro sottocartelle dirette (mai scansione disco).
- Salvataggio atomico (tmp + `os.replace`) con `.bak`; preservazione delle chiavi che l'editor non gestisce.
- Preset colori brand (Claude `#D97757`, Codex `#10A37F`, Copilot `#8B5CF6`, ...) → genera `bar_fg` + `icon_light.fg` / `icon_dark.fg` fondendo verso bianco/nero.
- Tab JSON grezzo in sola lettura, test del quick action con conferma.
- Limite attuale: edita solo 3 chiavi (`bar_fg`, colori icona, `quick_action_command`); nessun restart dell'istanza.

### 1.6 `hybrid2102/vsix-copilot-status-extension`

Estensione Visual Studio in C#. Fuori scope: non è un'istanza tray né un exe aggiornabile dal manager.

---

## 2. Stato della macchina (rilevato oggi)

| Istanza | PID (bootloader/app) | Exe | Versione file | Config dir | Note |
|---|---|---|---|---|---|
| Claude (default) | 32472 / 32520 | `D:\OneDrive\Software\Tools\AI\Claude\UsageMonitorForClaude.exe` | 1.22.0 (= latest) | `~/.claude` | avviata da explorer |
| Claude "valeria" | 82104 / 93020 | stesso exe | 1.22.0 | `~/.claude-valeria` | **nessun `--config-dir` sulla riga di comando**: il path arriva solo da `CLAUDE_CONFIG_DIR` nell'ambiente del processo |
| Copilot | 32440 / 17540 | `...\Copilot\UsageMonitorForCopilot.exe` | 0.3.1 (**latest 0.3.2**) | `~/.copilot` | |
| Codex | 83312 / 18204 | `...\Codex\UsageMonitorForCodex.exe` | 0.1.0 (= latest) | `~/.codex` | avviata da Everything.exe |

Altri fatti:

- Quattro `usage-monitor-settings.json` esistenti: `~/.claude`, `~/.claude-valeria`, `~/.codex`, `~/.copilot`.
- Registro `HKCU\...\Run` contiene 4 voci scritte dai monitor stessi (`UsageMonitorForClaude`, `UsageMonitorForClaude_578560bbc610` con `--config-dir="C:\Users\Marco\.claude-valeria"`, `UsageMonitorForCodex`, `UsageMonitorForCopilot`).
- Shortcut legacy `UsageMonitorForClaude.exe - valeria.lnk` con `--config-dir=%USERPROFILE%\.claude-valeria` (convenzione dell'updater PowerShell).
- `update-usage-monitor.bat` accanto all'exe Claude che invoca lo script PowerShell dal repo locale.
- Gli exe vivono **dentro OneDrive**: lo swap dei file deve tollerare i lock del client di sync.
- Verificato: `psutil.Process.environ()` legge `CLAUDE_CONFIG_DIR` dal processo figlio dell'istanza valeria. È la chiave per l'auto-discovery senza dipendere da shortcut.
- Nella tabella che precede, i figli `AgentMonitorForClaude.exe` (stessa cartella, stesso autore) non sono un usage-monitor ma sono un candidato naturale per la generalizzazione (vedi §9).

---

## 3. Obiettivi e non-obiettivi

**Obiettivi (dalla richiesta):**

1. Auto-discovery delle istanze in esecuzione (anche più d'una per exe), con versione, data ultimo aggiornamento, config dir, file settings, autostart, PID.
2. Riavvio singolo o multiplo.
3. Check aggiornamenti su GitHub rispetto alla versione locale, con anteprima changelog.
4. Download, verifica, sostituzione exe, riapertura delle istanze che erano attive.
5. Editing UI dei file di configurazione (tutte le chiavi documentate, per kind).
6. Autoupdate del manager stesso.
7. Sostituire completamente `usage-monitor-updater` e `usage-monitor-config-editor`.

Più tutte le proposte del §9, che entrano nel perimetro v1 (decisione del 2026-09-17).

**Non-obiettivi (prima release):**

- Modifiche ai monitor a monte (il manager si adatta a come sono, non richiede patch upstream).
- Linux: l'architettura lo prevede (layer `platform/`), l'implementazione è solo Windows nella v1.
- Firma Authenticode dei build del manager.

---

## 4. Decisioni architetturali

### 4.1 Riscrittura da zero

Il codice attuale (6 file, ~200 righe, `updater.apply_update` solleva `NotImplementedError`) non vale una migrazione: viene sostituito. Si tiene il nome del repo e del package, si azzera `src/`.

### 4.2 Stack

| Scelta | Motivazione |
|---|---|
| **Python 3.12**, `src/` layout, `pyproject.toml` | coerente con la famiglia, riuso mentale di chi la manutiene |
| **pystray** per la tray | già usato dai monitor, funziona con PyInstaller |
| **pywebview (WebView2)** per la finestra manager | stesso stack dei monitor: exe ~15 MB con PyInstaller invece degli 89 MB di PySide6+Nuitka; UI HTML/CSS/JS vanilla (nessun bundler), coerenza visiva con i popup dei monitor |
| **psutil** | process tree, `environ()`, `create_time`, terminate/wait |
| **ctypes** (no pywin32) | `GetFileVersionInfoW` per la versione PE, `WinVerifyTrust` opzionale, lettura Run key via `winreg` |
| **urllib** + `json` per GitHub | niente `requests`: meno superficie nell'exe; ETag/`If-None-Match` per il rate limit |
| **pydantic** o `dataclasses` + validazione manuale | preferenza: `dataclasses` + funzioni di validazione replicate da `settings._validate()` dei monitor, per non portare pydantic nell'exe |
| **PyInstaller** onefile + `version_info.py` | pipeline identica ai monitor; CI su `windows-latest`, release su tag `v*` con `.exe.sha256` |
| Tooling | `ruff`, `pyright` (strict come i monitor), `pytest`, `pre-commit` |

Alternativa scartata (deciso): PySide6. Pro: form nativi più rapidi da scrivere. Contro: 89 MB, Nuitka lento in CI, look diverso dai monitor.

Artefatti: exe `UsageMonitorManager.exe`, comando CLI `umm`, package `usage_monitor_manager`.

### 4.3 Principi

- **Core senza UI**: tutto (discovery, controllo processi, update, config) vive in moduli puri testabili, esposti da una CLI (`umm ...`) e consumati dalla tray/finestra. La UI è un client.
- **Kind registry**: un file dati descrive ogni tipo di monitor (exe, env var, default dir, repo, asset, schema settings). Aggiungere un futuro `usage-monitor-for-gemini` = aggiungere una voce.
- **Nessun stato implicito**: il manager ha un proprio `manager-config.json` (istanze note, etichette, preferenze) ma la verità sulle istanze in esecuzione viene sempre dal sistema.
- **Operazioni idempotenti e con rollback**: ogni step distruttivo (kill, swap file, save settings) ha `.bak`, retry e messaggio di ripristino.
- **Mai toccare ciò che non è nostro**: kill solo dei processi il cui exe path coincide; settings salvati preservando chiavi sconosciute.

### 4.4 Layout del package

```
src/usage_monitor_manager/
  __init__.py            __version__
  __main__.py            entry: tray (default) | CLI (umm ...) | --finish-self-update
  kinds.py               registro dei tipi di monitor (dati, non logica)
  model.py               dataclass: MonitorKind, Instance, ProcessPair, ReleaseInfo, UpdatePlan
  discovery.py           process scan + registry Run + settings files → list[Instance]
  process_control.py     start / stop / restart (singolo, multiplo, per exe)
  versions.py            PE ProductVersion (ctypes), parse/compare semver
  github.py              releases API, ETag cache, rate-limit, asset selection, download con progress
  integrity.py           sha256 (digest API / sidecar .sha256), Authenticode opzionale
  updater.py             UpdatePlan → esecuzione (stop, swap, restart) con rollback
  self_update.py         stesso engine applicato al manager (rename-swap + relaunch)
  settings/
    schema.py            schema per kind (chiavi, tipo, default, sezione, help)
    store.py             load/validate/save atomico + .bak, chiavi sconosciute preservate
    presets.py           palette brand (da config-editor)
  manager_config.py      stato persistente del manager (%LOCALAPPDATA%\UsageMonitorManager\)
  secrets.py             token GitHub: Windows Credential Manager (ctypes CredRead/CredWrite), fallback env
  watchdog.py            polling istanze: crash, duplicati, bootloader orfani
  archive.py             cronologia exe (.umm-archive) e rollback
  history.py             storico datato dei settings + diff + export/import
  profiles.py            profili di avvio (set di istanze)
  scheduler.py           check update periodico, notifiche
  platform/
    win32.py             no-window Popen, winreg Run (monitor e manager), file version, WinVerifyTrust, hotkey
    linux.py             stub (flock, XDG) per una fase futura
  ui/
    tray.py              pystray: menu dinamico, azioni rapide, notifiche
    window.py            pywebview: dashboard, changelog, editor settings
    web/                 index.html, app.css, app.js (vanilla), icone
  cli.py                 umm list|restart|check|update|config|self-update  (--json)
tests/                   pytest, fixture per process tree finto, API GitHub finta, fs temporaneo
build.py, usage_monitor_manager.spec, version_info.py
.github/workflows/ci.yml, release.yml
```

---

## 5. Modello di dominio

```
MonitorKind      key ("claude"|"copilot"|"codex"), display_name, exe_name, pe_product_name,
                 env_var, default_config_dir, repo ("owner/name"), asset_pattern, sha_sidecar: bool,
                 settings_schema_id, mutex_base, run_key_base

Instance         kind, config_dir (resolved), instance_suffix ("" | "_<12hex>"),
                 exe_path, exe_version (PE), exe_mtime, settings_path (risolto con la stessa
                 search-order dei monitor) + settings_exists, autostart_entry (bool, comando),
                 launch_args (--config-dir…), launch_env ({VAR: dir}), label (utente),
                 processes: ProcessPair | None  (bootloader_pid, app_pid, started_at)

ReleaseInfo      tag, version, published_at, html_url, body_markdown, asset (name, url, size,
                 digest | None), sidecar_sha_url | None, is_prerelease

UpdatePlan       exe_path, kind, current_version, target: ReleaseInfo,
                 affected_instances: list[Instance]   (tutte quelle che usano quell'exe)
```

Concetto chiave: **un aggiornamento è per exe, un riavvio è per istanza**. Le due istanze Claude condividono lo stesso file: l'update le ferma entrambe e le riavvia entrambe con i rispettivi `config_dir`.

---

## 6. Design dei moduli

### 6.1 Discovery (`discovery.py`)

1. **Scan processi** con psutil: candidati = processi il cui `exe()` ha basename presente nel kind registry **oppure** il cui PE ProductName inizia con `Usage Monitor for` (cattura futuri kind e rinomine).
2. **Collasso PyInstaller**: se un candidato ha come padre un processo con lo stesso exe, è l'"app"; il padre è il bootloader. Si tiene la coppia. Un processo orfano (padre morto) è comunque l'app.
3. **Config dir** per ogni app: `--config-dir` dalla cmdline (stessa normalizzazione di `parse_config_dir`: quote, `%VAR%`, `~`, trailing slash) → altrimenti `environ()[env_var]` → altrimenti default. Se `environ()` fallisce (AccessDenied) si segnala `config_dir_unknown`.
4. **Suffix** = replica esatta di `config_dir_suffix()` (sha1 su `os.path.normcase(str(Path.resolve()))`), così i nomi mutex/Run coincidono con quelli dei monitor: permette di trovare la Run key e, in futuro, leggere l'`HolderPID` mapping.
5. **Settings path**: replica della search order (`config_dir/` se non default → `exe_dir/` → `default_dir/`), riportando *quale* delle tre è attiva e le altre due come "ombra" (utile per spiegare perché una modifica non ha effetto).
6. **Istanze dormienti**: unione con (a) voci `HKCU\...\Run\UsageMonitorFor*`, (b) shortcut `<exe> - *.lnk` accanto agli exe noti (import legacy), (c) `usage-monitor-settings.json` trovati con la discovery del config-editor, (d) istanze salvate in `manager-config.json`. Ogni sorgente è etichettata nel risultato.
7. Output stabile e serializzabile (`umm list --json`), ordinato per kind e config dir.

### 6.2 Controllo processi (`process_control.py`)

- `stop(instance, timeout=15)`: `terminate()` sull'app pid, poi sul bootloader, `wait`; fallback `kill()`. Verifica che il mutex sia stato rilasciato (tentativo `CreateMutexW` + `ERROR_ALREADY_EXISTS`) prima di dichiarare "fermata".
- `start(instance)`: `Popen([exe, *launch_args], env={**os.environ, **launch_env}, cwd=exe_dir, creationflags=DETACHED_PROCESS|CREATE_NO_WINDOW)`. Preferenza: passare `--config-dir` esplicito anche se l'istanza originale usava solo l'env var, così la nuova istanza è auto-descrittiva e la Run key che il monitor riscrive resta coerente.
- `restart(instances)`: stop di tutte → start in sequenza con 2 s di distanza (come l'updater) per evitare collisioni con WebView2.
- `restart_all()`, `stop_all()`, `start_missing()` (avvia le dormienti selezionate).

### 6.3 Aggiornamenti (`github.py`, `integrity.py`, `updater.py`)

- `check(kind)`: `GET /repos/{repo}/releases/latest` (o `/releases` se si vuole includere prerelease) con `User-Agent`, `If-None-Match` (cache su disco: etag + body + timestamp), token opzionale da `GITHUB_TOKEN`/manager-config. Rate limit → messaggio con orario di reset, mai eccezione non gestita.
- Confronto: `PE ProductVersion` (3 componenti) vs tag `vX.Y.Z`. Stati: `up_to_date`, `update_available`, `newer_local` (build locale), `unknown` (PE senza versione).
- Changelog: `body` della release, reso in HTML nella finestra (markdown minimale: heading, liste, link, codice) e come testo nella CLI. Se `body` è vuoto, fallback al `CHANGELOG.md` del repo alla sezione della versione.
- Asset: `.exe` con euristica architettura dell'updater; sha dal `digest` API, altrimenti dal sidecar `.exe.sha256`, altrimenti "non verificabile" (richiede conferma esplicita).
- Esecuzione (`UpdatePlan.apply(progress_cb)`):
  1. download in `exe_dir/<name>.download` con progress e verifica dimensione;
  2. sha256 → mismatch = abort, file cancellato, nulla toccato;
  3. Authenticode via `WinVerifyTrust` (opzionale, info-only: le build sono unsigned);
  4. stop di tutte le `affected_instances`;
  5. `exe → exe.bak` (retry 5×2 s), `.download → exe` (retry), su fallimento ripristino `.bak`;
  6. restart delle istanze che erano in esecuzione, con i loro `config_dir`;
  7. verifica post-avvio (processo vivo dopo 5 s, versione PE = target); cleanup `.bak` solo a verifica riuscita, altrimenti offerta di **rollback** (stop, `.bak → exe`, restart).
- Ogni step emette eventi (`UpdateEvent`) consumati da CLI (log) e UI (barra + changelog visibile durante il download, come nello script PowerShell).

### 6.4 Editor configurazione (`settings/`)

- **Schema per kind** dichiarativo: per ogni chiave `type` (int, bool, hex-color, rgba-dict, string-list, wildcard-list, command, threshold-list, enum, cli-command-map), `default`, `min`, `section`, `help` (testo preso da `docs/configuration.md`), `applies_to` (kinds). Le chiavi con prefisso (`alert_thresholds_*`) sono un tipo "prefix family" con suggerimenti dei field noti per kind.
- **Validazione** = porting di `_validate()` dei monitor (stesse regole, stessi messaggi), eseguita prima del salvataggio: l'utente non deve scoprire l'errore dal MessageBox del monitor al riavvio.
- **Store**: `utf-8-sig` in lettura, `utf-8` + newline `\n` in scrittura, atomic replace + `.bak`; **chiavi sconosciute preservate** e mostrate in una sezione "Altre chiavi (raw)".
- **UI**: form generato dallo schema con sezioni collassabili, ricerca chiave, "reset al default" per chiave, tab JSON grezzo editabile con validazione live, diff "salvato vs modificato". Bottoni: *Salva*, *Salva e riavvia istanza*, *Testa quick action* (con conferma), *Apri cartella*, *Crea file* (per istanze senza settings: propone la posizione giusta della search order).
- **Preset brand** e generatore palette dal config-editor; anteprima swatch bar/icona chiara/scura.
- **Copia impostazioni** tra istanze dello stesso kind (es. da Claude default a Claude valeria) con selezione delle sezioni.

### 6.5 Autoupdate del manager (`self_update.py`)

- Stesso engine (`repo = hybrid2102/usage-monitor-manager`, asset `UsageMonitorManager.exe` + `.sha256`).
- Su Windows un exe in esecuzione **si può rinominare ma non sovrascrivere**: sequenza = download `.download` → verifica → `manager.exe → manager.exe.bak` → `.download → manager.exe` → `Popen(manager.exe --post-update <old_pid>)` → uscita. Il nuovo processo attende la morte di `<old_pid>`, cancella `.bak`, notifica "aggiornato alla vX.Y.Z".
- Il manager è singleton (mutex `UsageMonitorManager_SingleInstance`); l'avvio con `--post-update` è l'unica eccezione che attende invece di uscire.
- Check automatico all'avvio + ogni 24 h (configurabile, disattivabile); mai applicato senza clic esplicito.
- **Autostart del manager** (deciso: incluso): toggle in preferenze che scrive/rimuove `HKCU\...\Run\UsageMonitorManager` = `"<exe>" --tray`; il path viene risincronizzato ad ogni avvio (l'exe è in OneDrive e può muoversi), come fa `sync_autostart_path` nei monitor. Default: disattivo al primo avvio, proposto nel wizard di import.

### 6.5-bis Token GitHub (`secrets.py`)

Deciso: campo opzionale. Salvato nel **Windows Credential Manager** (target `UsageMonitorManager/github`, via `advapi32.CredWriteW`/`CredReadW` in ctypes, nessuna dipendenza `keyring`); `manager-config.json` conserva solo il flag "token presente". Ordine di risoluzione: Credential Manager → env `GITHUB_TOKEN`/`GH_TOKEN` → anonimo. La UI mostra lo stato del rate limit (`X-RateLimit-Remaining`/`Reset`) per rendere visibile il beneficio.

### 6.6 Tray e finestra (`ui/`)

**Tray (pystray)**: icona con badge (nessun update / N update disponibili / errore). Menu:

```
Istanze
  ├ Claude · ~/.claude          1.22.0  ● in esecuzione     ▸ Riavvia | Ferma | Impostazioni… | Apri cartella
  ├ Claude · ~/.claude-valeria  1.22.0  ● in esecuzione     ▸ …
  ├ Copilot · ~/.copilot        0.3.1   ● ↑ 0.3.2 disponibile ▸ … | Aggiorna…
  └ Codex · ~/.codex            0.1.0   ○ non in esecuzione ▸ Avvia | …
Riavvia tutte · Ferma tutte · Avvia mancanti
Controlla aggiornamenti (ultimo check: 12:40)
Apri Manager…
Manager 1.0.0 (aggiornato) · Esci
```

**Finestra (pywebview)**: dashboard a tabella (kind, label, config dir, versione locale/remota, data exe, PID, uptime, settings attivo, autostart), pannello changelog con bottone *Aggiorna* e log dell'operazione, editor impostazioni (§6.4), pagina "Manager" (versione, self-update, preferenze, token GitHub, log).

### 6.7 CLI (`cli.py`)

Serve a test, script e Task Scheduler prima ancora che la UI esista:

```
umm list [--json]
umm restart (--all | --kind claude | --config-dir PATH | --id ...)
umm stop / start ...
umm check [--kind ...] [--json]         # exit 10 se ci sono update
umm update --kind copilot [--yes] [--no-restart]
umm config show|edit|validate|set KEY VALUE --instance ...
umm self-update [--check-only]
```

---

## 7. Fasi di lavoro

Ogni fase chiude con test verdi in CI e un tag `v0.N.0` installabile.

| Fase | Contenuto | Uscita |
|---|---|---|
| **0. Reset** | Svuotare `src/`, nuovo layout, `pyproject`, ruff/pyright/pytest/pre-commit, CI su `windows-latest`, `kinds.py`, `model.py`, README riscritto | `v0.1.0` (scheletro) |
| **1. Discovery + versioni** | `discovery.py`, `versions.py`, `platform/win32.py`, `umm list --json`; test con process tree finto e file PE di fixture | `v0.2.0` |
| **2. Controllo processi** | `process_control.py`, `umm restart/stop/start`; test di integrazione con un exe dummy (script Python congelato) | `v0.3.0` |
| **3. Update engine** | `github.py`, `integrity.py`, `updater.py`, `umm check/update` con changelog in console; test con API finta (fixture JSON reali delle 3 release) e rollback simulato | `v0.4.0` — **qui `usage-monitor-updater` è già rimpiazzabile** |
| **4. Tray + dashboard** | `ui/tray.py`, `ui/window.py`, `web/`, badge, notifiche, changelog HTML, progress; build PyInstaller + `version_info.py` + `release.yml`; **§9.1 notifica update periodica**, **§9.3 import legacy** (wizard al primo avvio), **§9.8 gestione autostart** (istanze + manager) | `v0.5.0` |
| **5. Editor impostazioni** | schema dei 3 kind, validazione, store, form UI, preset, copia tra istanze; import di `profiles.json` del config-editor; **§9.6 storico/diff/export dei settings** | `v0.6.0` — **qui `usage-monitor-config-editor` è rimpiazzabile** |
| **6. Self-update e robustezza** | `self_update.py`, `--post-update`, check periodico, token in Credential Manager; **§9.2 watchdog**, **§9.4 archivio exe + rollback**, **§9.5 prerelease / salta versione**, **§9.10 `umm update --all --yes` per Task Scheduler** | `v0.7.0` |
| **7. Estensioni** | **§9.9 kind generico "exe da GitHub Releases"** con `AgentMonitorForClaude` come primo caso, **§9.7 modalità diagnostica** (`--verbose` su file + viewer), **§9.11 `status.json` + `umm status`**, **§9.13 profili di avvio**, **§9.14 hotkey globale** (via `RegisterHotKey` in ctypes, senza `pynput`) | `v0.8.0` |
| **8. Rifinitura e dismissione** | i18n it/en, log su file, gestione errori uniforme, README/doc utente, **§9.12 layer `platform/linux.py` come stub documentato** (solo predisposizione); nei due repo vecchi: README con avviso di archiviazione + link, poi *archive* su GitHub (dopo la 1.0) | `v1.0.0` |

Stima ordine di grandezza: fasi 0–3 sono il cuore e stanno in poche sessioni; 4, 5 e 6 sono le più lunghe; 7 e 8 medie. Le fasi 0–3 non dipendono da nessuna decisione UI e possono partire subito.

---

## 8. Rischi e mitigazioni

| Rischio | Mitigazione |
|---|---|
| `psutil.environ()` negato (processo elevato, altro utente) | fallback a cmdline → Run key con lo stesso exe → manager-config; istanza marcata "config dir non determinabile" invece di indovinare |
| OneDrive tiene il lock sull'exe durante lo swap | retry con backoff (5×2 s), messaggio chiaro, `.bak` sempre ripristinabile; opzione "pausa sync" solo documentata |
| Antivirus mette in quarantena l'exe scaricato | verifica presenza file dopo il download; errore esplicito con path |
| Rate limit API GitHub (60/h) con più kind + self-update | ETag cache, un solo check per repo per ora, token opzionale |
| Upstream cambia nome asset/exe o schema settings | kind registry versionato; chiavi sconosciute preservate; test che leggono le fixture aggiornabili con uno script `tools/refresh_fixtures.py` |
| Due processi PyInstaller: kill del solo figlio lascia il bootloader | stop sempre sulla coppia; discovery segnala coppie incomplete |
| Il monitor riscrive la Run key ad ogni avvio | il manager non gestisce l'autostart dei monitor: lo **mostra** e offre solo toggle esplicito |
| Regressioni UI difficili da testare | tutta la logica nel core con test; la UI chiama solo funzioni del core; smoke test manuale prima di ogni tag |

---

## 9. Proposte di funzionalità aggiuntive

**Decisione 2026-09-17: entrano tutte nella v1**, distribuite nelle fasi 4–8 (vedi §7). Ordinate per rapporto valore/costo, a mio giudizio.

1. **Notifica update automatica**: check periodico (default 6 h) e toast "Copilot 0.3.2 disponibile" con clic → changelog. Rende inutile lo script `check-update.ps1` proposto dalla doc upstream.
2. **Watchdog istanze**: rilevare istanza crashata (era in esecuzione, non lo è più) e offrire/eseguire il riavvio; rilevare **duplicati anomali** (due app pid con lo stesso suffix) o bootloader orfani e proporre pulizia.
3. **Import legacy**: al primo avvio importa shortcut `<exe> - *.lnk`, Run key e `profiles.json` del config-editor in `manager-config.json`, con etichette suggerite (`.claude-valeria` → "Claude Valeria").
4. **Rollback e cronologia versioni**: conservare gli ultimi N exe in `exe_dir/.umm-archive/` con manifest; menu "Torna alla 1.21.0". Utile quando una release upstream rompe qualcosa.
5. **Canale prerelease e "salta versione"** per exe, come nei package manager.
6. **Storico e diff dei settings**: ogni salvataggio archivia una copia datata; UI con diff e ripristino; esportazione/importazione profilo (`.json`) per replicare la configurazione su un'altra macchina.
7. **Modalità diagnostica**: avviare un'istanza con `--verbose` catturando stdout su file, viewer del log nella finestra, bottone "copia diagnostica" per aprire issue upstream.
8. **Gestione autostart esplicita**: toggle per istanza sulla Run key (rispettando il naming del monitor) e stato "all'avvio di Windows: sì/no" in dashboard.
9. **App gestite oltre la famiglia**: il kind registry può descrivere anche `AgentMonitorForClaude.exe` (stesso autore, stessa cartella, stesso modello di release) e in generale qualsiasi "exe portabile aggiornato da GitHub Releases": il manager diventa un mini-package-manager per la cartella `Tools\AI`.
10. **Aggiornamento pianificato**: `umm update --all --yes` da Task Scheduler con log e notifica di esito, per chi non vuole cliccare (compatibile con il `-NonInteractive` dello script vecchio).
11. **Health API locale**: `umm status --json` e un file `status.json` aggiornato dalla tray, consumabile da statusline/script (es. il `statusline.mjs` che hai nella stessa cartella).
12. **Supporto Linux**: i monitor girano anche su Linux (flock + XDG); con il layer `platform/` la discovery e il controllo processi sono portabili, l'update dovrebbe gestire "run from source" invece dell'exe.
13. **Profili di avvio**: "Lavoro" = Claude+Copilot, "Personale" = Claude valeria+Codex; un clic avvia/ferma il set.
14. **Hotkey globale** per aprire la finestra manager (basso costo con `pynput`, ma aggiunge una dipendenza: da valutare).

---

## 10. Decisioni prese (2026-09-17)

| # | Domanda | Decisione |
|---|---|---|
| 1 | Stack UI | **pywebview + HTML** (pystray per la tray) |
| 2 | Nomi | exe `UsageMonitorManager.exe`, CLI `umm`, package `usage_monitor_manager` |
| 3 | Repo release per Claude | `jens-duttke/usage-monitor-for-claude` (il fork hybrid2102 non pubblica release) |
| 4 | Token GitHub | campo opzionale, salvato nel Windows Credential Manager (§6.5-bis) |
| 5 | Linux | solo predisposizione architetturale (`platform/` + stub) |
| 6 | Autostart del manager | **incluso**, toggle in preferenze (§6.5) |
| 7 | Proposte §9 | **tutte in v1**, distribuite nelle fasi 4–8 |
| 8 | Repo vecchi | archiviazione GitHub con README di rimando **dopo la 1.0** |

Prossimo passo: avvio della Fase 0 (reset di `src/`, scaffolding, tooling, CI) su approvazione esplicita.
