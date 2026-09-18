# usage-monitor-manager

Tray app (e CLI `umm`) che unifica la gestione delle istanze della famiglia
**Usage Monitor**: scoperta automatica, riavvio, controllo e installazione degli
aggiornamenti da GitHub, editing dei file di configurazione e autoupdate del
manager stesso.

Sostituisce `usage-monitor-updater` (PowerShell) e `usage-monitor-config-editor`
(PySide6) con un unico strumento Python.

## Istanze gestite

| Istanza | Config dir | Env var | Repo delle release |
|---|---|---|---|
| Claude (default) | `~/.claude` | `CLAUDE_CONFIG_DIR` | `jens-duttke/usage-monitor-for-claude` |
| Claude "valeria" | `~/.claude-valeria` | `CLAUDE_CONFIG_DIR` | come sopra (stesso exe) |
| Copilot | `~/.copilot` | `COPILOT_HOME` | `hybrid2102/usage-monitor-for-copilot` |
| Codex | `~/.codex` | `CODEX_HOME` | `hybrid2102/usage-monitor-for-codex` |

Un aggiornamento è **per exe**, un riavvio è **per istanza**: le due istanze
Claude condividono lo stesso eseguibile.

## Documentazione

Il piano di sviluppo completo (ricognizione, decisioni architetturali, design
dei moduli, fasi) è in [PLAN.md](PLAN.md).

## Setup di sviluppo

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

Controlli usati anche in CI (`windows-latest`, Python 3.12):

```powershell
ruff check .
ruff format --check .
pyright
pytest
```

Facoltativo: `pre-commit install` per eseguire ruff a ogni commit.

## Uso

```
umm --version
umm list [--json]
umm restart (--all | --kind claude | --config-dir PATH | --id ...)
umm stop / start ...
umm check [--kind ...] [--changelog] [--prerelease] [--json]
umm update (--kind copilot | --all) [--yes] [--no-restart] [--dry-run] [--json]
umm rollback --kind copilot [--no-restart] [--json]
umm config show|edit|validate|set KEY VALUE --instance ...
umm self-update [--check-only]
umm status [--json]
umm tray
umm import-legacy [--dry-run] [--json]
umm autostart --manager [on|off|status] [--json]
umm autostart --instance ID [on|off|status] [--json]
```

Senza argomenti, `python -m usage_monitor_manager` avvia la modalità tray
(`umm tray` fa lo stesso; `--tray`, la forma scritta nella Run key secondo
PLAN.md §6.5, è accettata come alias).

### Tray

L'icona nella system tray è il punto d'ingresso quotidiano. Il menu viene
ricostruito a ogni apertura e mostra:

```
Claude · ~/.claude          1.22.0  ● in esecuzione        ▸ Riavvia | Ferma | Avvia | Impostazioni… | Apri cartella
Claude Valeria · ~/.claude-valeria  1.22.0  ● in esecuzione ▸ …
Copilot · ~/.copilot        0.3.1   ● in esecuzione  ↑ 0.3.2 disponibile ▸ … | Aggiorna a 0.3.2…
Codex · ~/.codex            0.1.0   ○ non in esecuzione     ▸ Avvia | …
---
Riavvia tutte · Ferma tutte · Avvia mancanti
Controlla aggiornamenti (ultimo: 12:40)
Apri Manager…
---
Manager 0.5.0 · Esci
```

L'icona porta un badge con il numero di aggiornamenti disponibili (o `!` se
l'ultimo controllo è fallito) e le notifiche di Windows annunciano ogni nuova
release una volta sola. Il manager è singleton (mutex
`UsageMonitorManager_SingleInstance`): avviarlo una seconda volta stampa un
avviso ed esce.

### Finestra

«Apri Manager…» apre la finestra pywebview (tema scuro coerente con i popup dei
monitor). Viene creata una sola volta e nascosta alla chiusura, così
riaprirla è istantaneo. Tre pagine:

- **Dashboard** — tabella con tipo, etichetta, config dir, versione locale e
  remota, data dell'exe, PID, uptime, file di impostazioni attivo e toggle di
  autostart; per riga: Riavvia / Ferma / Avvia / Impostazioni / Cartella e,
  quando serve, Aggiorna. Il pannello «Impostazioni» mostra per ora il JSON in
  **sola lettura** (l'editor con i campi validati è la Fase 5).
- **Aggiornamenti** — changelog della release reso da un renderer markdown
  inline, barra di progresso del download, log degli eventi dell'update e i
  pulsanti «Aggiorna» e «Rollback».
- **Manager** — versione, preferenze (avvio con Windows, notifiche,
  prerelease, intervallo di controllo), **presenza** del token GitHub e stato
  del rate limit, importazione della configurazione precedente e log.

Nota di architettura: pywebview possiede il **main thread** e pystray gira su
un thread di servizio, il contrario di quello che fanno i monitor. Il motivo è
documentato in `ui/window.py`: la finestra del manager è persistente, quindi il
loop che la possiede dev'essere quello principale del processo.

### Configurazione e importazione

Lo stato del manager vive in
`%LOCALAPPDATA%\UsageMonitorManager\manager-config.json` (salvataggio atomico
con `.bak`): istanze dichiarate ed etichettate, preferenze, `last_check` per
repo e `skipped_versions`. Al primo avvio il manager importa una sola volta la
configurazione precedente — shortcut `<exe> - *.lnk`, voci `HKCU\...\Run` e
`profiles.json` del vecchio config editor — proponendo le etichette:

```
> umm import-legacy --dry-run
umm import-legacy --dry-run: nessuna modifica verra' scritta
  [profiles] Claude: C:\Users\Marco\.claude  (profiles.json (Claude))
  [profiles] Claude Valeria: C:\Users\Marco\.claude-valeria  (profiles.json (Claude Valeria))
  [profiles] Copilot: C:\Users\Marco\.copilot  (profiles.json (Copilot))
  [profiles] Codex: C:\Users\Marco\.codex  (profiles.json (Codex))
da aggiungere: 4
```

Nulla viene rimosso o modificato nelle sorgenti legacy: il manager le legge e
basta.

### Build

```powershell
.\.venv\Scripts\python build.py --clean --sha
```

Produce `dist\UsageMonitorManager.exe` (PyInstaller onefile, senza console,
asset `ui/web` inclusi, icona generata da `tools/generate_icon.py` e
`version_info.py` generato da `__version__`, così la `ProductVersion` del PE è
leggibile come per i monitor). Su tag `v*` il workflow di release ricostruisce
tutto su `windows-latest` e pubblica l'exe con il suo `.sha256`, usando la
sezione corrispondente di `CHANGELOG.md` come note.

## Stato

**Fase 4 (Tray + dashboard) completata** (`v0.5.0`): il manager ha ora una
tray, una finestra, il controllo aggiornamenti periodico, la gestione
dell'autostart e una pipeline di build/release. Dalla fase precedente
**`usage-monitor-updater` (PowerShell) è sostituibile**: confronto versioni,
changelog, download verificato, swap con `.bak`, riavvio delle istanze e
rollback sono coperti da `umm check` / `umm update` / `umm rollback` e dalla
pagina Aggiornamenti.

Oltre allo scheletro della Fase 0 (`kinds.py`, `model.py`, `identity.py`,
tooling, CI) sono reali e testati:

- `platform/` — dispatcher su `sys.platform`; `win32.py` implementa la lettura
  della versione PE (`ProductVersion` / `FileVersion` / `VS_FIXEDFILEINFO` via
  `version.dll` in ctypes), il `ProductName`, le voci di `HKCU\...\Run` (winreg)
  con parsing dei comandi quotati, la lettura in blocco degli shortcut `.lnk`
  (una sola chiamata a `WScript.Shell`), i flag Popen senza finestra, la sonda
  del mutex singleton (`mutex_is_held`: `CreateMutexW` +
  `ERROR_ALREADY_EXISTS`, handle chiuso subito) e lo stato Authenticode
  (`WinVerifyTrust` in ctypes, solo informativo);
  `linux.py` resta uno stub documentato;
- `versions.py` — parsing tollerante (`v1.22.0`, `1.22.0.0` → `1.22.0`),
  confronto, `display_version` (3 componenti, usata anche in `umm list`) e
  stati `up_to_date` / `update_available` / `newer_local` / `unknown`;
- `discovery.py` — scan dei processi con psutil, collasso delle coppie
  bootloader/app di PyInstaller, risoluzione del config dir (`--config-dir` →
  env var → default, con la sorgente registrata e `unknown` su AccessDenied),
  suffisso di istanza, replica della search order dei settings con i file "in
  ombra", autostart per suffisso, istanze dormienti da Run key, shortcut legacy
  e `usage-monitor-settings.json` sparsi;
- `process_control.py` — `stop` (terminate dell'app poi del bootloader, `wait`,
  fallback `kill`, attesa del rilascio del mutex), `start` (Popen detached e
  senza finestra, `--config-dir` sempre esplicito per le directory non di
  default, ri-discovery di conferma, rifiuto se il mutex è già occupato),
  `restart`, `stop_all` / `restart_all` / `start_missing`; risultati come
  dataclass frozen con `to_dict()` e progresso via `on_event(ProcessEvent)`;
- `github.py` — client `urllib` delle release (`User-Agent`,
  `Accept: application/vnd.github+json`, `Authorization: Bearer` opzionale),
  cache ETag su disco con `If-None-Match` e riuso del body sul `304`, rate
  limit → `RateLimitedError(reset_at)` con lo stato `X-RateLimit-*` esposto,
  selezione dell'asset (porting di `Select-ReleaseAsset`, euristiche di
  architettura), `digest` dell'API e sidecar `.sha256`, download in streaming
  con progress e controllo della dimensione. Token: parametro →
  `GITHUB_TOKEN` / `GH_TOKEN` → anonimo (Credential Manager alla Fase 6);
- `integrity.py` — `sha256_file`, `verify_digest`, `parse_sidecar` (formati
  `<hex>  <nome>` e hex nudo) e `authenticode_status`
  (`signed_valid` / `not_signed` / `invalid(<codice>)` / `unavailable`): la
  firma è **solo informativa**, le build upstream sono unsigned;
- `updater.py` — `plan_updates` (un piano **per exe**: le due istanze Claude
  condividono il file e finiscono nello stesso piano), `apply` con la sequenza
  della §6.3 (download → verifica → Authenticode → stop → `.bak` →
  swap → riavvio delle sole istanze che erano attive → verifica dopo 5 s →
  pulizia del `.bak`), `rollback`, eventi `UpdateEvent` per CLI e UI, e
  `FileSwapper` con retry 5x2 s e ripristino del `.bak` su errore. Nessuna
  operazione sui processi fuori da `process_control`; un asset senza sha256
  richiede una conferma esplicita, altrimenti l'update viene annullato e il
  download cancellato;
- `umm list [--json]` — tabella leggibile (con fallback ASCII sulle console
  cp1252) o dump JSON di `Instance.to_dict()`;
- `umm restart|stop|start` — selettori `--all`, `--kind` (ripetibile),
  `--config-dir` (ripetibile), `--id KIND:SUFFIX`, più `--json` e `--dry-run`;
  uscita 0 se tutto è andato bene, 1 se un'operazione è fallita, 3 se nessuna
  istanza corrisponde ai selettori;
- `umm check|update|rollback` — tabella kind/exe/locale/remota/stato/data,
  `--changelog` (link markdown resi come `testo (url)`), changelog stampato
  **prima** del download e progresso ridisegnato su una sola riga quando stdout
  è un TTY, `--dry-run` che elenca ogni passo senza toccare nulla; uscita 10 da
  `umm check` se c'è un aggiornamento, 3 su errore di rete o rate limit.

Tre regole di sicurezza governano il controllo processi:

1. si termina solo un PID il cui `exe()` coincide (`os.path.normcase`) con
   quello dell'istanza, verificato di nuovo subito prima di `terminate()` e di
   `kill()`; mai per nome del processo, e mai il processo corrente o un suo
   antenato;
2. "fermata" significa **mutex rilasciato**: finché il singleton risulta
   occupato `stop` non dichiara successo e `start` si rifiuta di partire;
3. l'ambiente del figlio non eredita mai né le variabili `_PYI_*` / `_MEI*` del
   bundle PyInstaller del padre (causano *Failed to load Python DLL*) né la env
   var del kind: viene rimossa per il config dir di default e impostata
   esplicitamente per gli altri.

L'update aggiunge le proprie:

4. si aggiorna **un exe**, si riavvia **un'istanza**: il piano ferma e riavvia
   tutte le istanze che usano quel file, e solo quelle che erano in esecuzione;
5. l'exe nuovo arriva nella stessa cartella del vecchio (`.download`), così lo
   swap finale è un rename; il `.bak` resta finché il controllo post-avvio non
   riesce, e `umm rollback --kind K` lo rimette al suo posto;
6. sha256 mancante = update rifiutato salvo conferma esplicita; sha256 diverso =
   update annullato e file scaricato cancellato, senza toccare l'exe.

Esempio su una macchina con quattro istanze attive:

```
KIND     CONFIG DIR                      VERSION  RUN  PIDS         AUTO  SOURCES
Claude   C:\Users\Marco\.claude          1.22.0   ●    32472/32520  Y     process,run_key,settings_file
Claude   C:\Users\Marco\.claude-valeria  1.22.0   ●    82104/93020  Y     process,run_key,shortcut,settings_file
Copilot  C:\Users\Marco\.copilot         0.3.1    ●    32440/17540  Y     process,run_key,settings_file
Codex    C:\Users\Marco\.codex           0.1.0    ●    83312/18204  Y     process,run_key,settings_file
```

Il controllo aggiornamenti sulla stessa macchina (uscita 10):

```
> umm check
KIND     EXE                                                               LOCAL   LATEST  STATUS            PUBLISHED
Claude   D:\OneDrive\Software\Tools\AI\Claude\UsageMonitorForClaude.exe    1.22.0  1.22.0  up_to_date        2026-09-03
Copilot  D:\OneDrive\Software\Tools\AI\Copilot\UsageMonitorForCopilot.exe  0.3.1   0.3.2   update_available  2026-09-17
Codex    D:\OneDrive\Software\Tools\AI\Codex\UsageMonitorForCodex.exe      0.1.0   0.1.0   up_to_date        2026-09-07
```

E l'anteprima di un aggiornamento, che non tocca nulla:

```
> umm update --kind copilot --dry-run
  would update Copilot 0.3.1 -> 0.3.2 (v0.3.2) at D:\...\UsageMonitorForCopilot.exe
  would download UsageMonitorForCopilot.exe (15260446 bytes) to D:\...\UsageMonitorForCopilot.exe.download
  would verify sha256 against the GitHub API digest
  would read the Authenticode state of UsageMonitorForCopilot.exe.download (information only)
  would stop copilot pids 7860, 32048 then wait for the UsageMonitorForCopilot_SingleInstance mutex
  would rename UsageMonitorForCopilot.exe to UsageMonitorForCopilot.exe.bak (retry 5x2s)
  would rename UsageMonitorForCopilot.exe.download to UsageMonitorForCopilot.exe, restoring the .bak on failure
  would start copilot: D:\...\UsageMonitorForCopilot.exe
  would check after 5s that the instances run and the PE version is 0.3.2, then delete the .bak
```

Dalla Fase 4 sono inoltre reali e testati:

- `manager_config.py` — `manager-config.json` con salvataggio atomico e `.bak`,
  validazione tollerante di ogni campo, conservazione delle chiavi sconosciute
  e istanze dichiarate dall'utente (sorgente «d» della discovery, §6.1, da cui
  arrivano le etichette);
- `scheduler.py` — controllo periodico (30 s dopo l'avvio, poi ogni
  `check_interval_hours`), stato in memoria per la UI, `last_check` per repo,
  una sola notifica per `(repo, tag)` e rispetto di `skipped_versions`;
  orologio, attesa e provider dei piani sono iniettabili;
- `legacy.py` — importazione una tantum da shortcut, Run key e `profiles.json`,
  con etichette suggerite (`.claude-valeria` → «Claude Valeria») e report
  identico fra `--dry-run` ed esecuzione reale;
- `ui/tray.py` — menu dinamico, badge, notifiche, operazioni su thread di
  lavoro e singleton via mutex;
- `ui/window.py` + `ui/web/` — `JsApi` (client puro dei moduli core, testato
  senza webview) e la UI a tre pagine;
- autostart — `set_manager_autostart` / `sync_manager_autostart_path` per il
  manager e `process_control.set_instance_autostart` per le istanze, con la
  radice di registro iniettabile (i test non toccano mai `HKCU\...\Run`).

I moduli delle fasi successive (editor impostazioni, self-update, watchdog,
archivio, storico) sono ancora stub con docstring di responsabilità e sono
descritti in PLAN.md §7.
