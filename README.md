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
umm rollback --kind copilot [--version 0.3.1] [--no-restart] [--json]
umm archive list|prune [--kind ...] [--keep N] [--json]
umm skip --kind copilot|manager   /   umm unskip --kind copilot|manager
umm config show|validate|set KEY VALUE|unset KEY|create|export DEST|import SRC|history|diff [N]|restore N --instance ID
umm token set|clear|status [--from-env VAR] [--json]
umm self-update [--check-only] [--yes] [--dry-run] [--json]
umm status [--json] [--check]
umm tray
umm import-legacy [--dry-run] [--json]
umm autostart --manager [on|off|status] [--json]
umm autostart --instance ID [on|off|status] [--json]
```

Codici di uscita: `0` tutto bene, `1` almeno un'operazione fallita, `3` nessuna
istanza corrispondente / errore di rete, `4` aggiornamento saltato perche'
l'asset non e' verificabile, `10` (`umm check`) ci sono aggiornamenti.

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
riaprirla è istantaneo. Quattro pagine:

- **Dashboard** — tabella con tipo, etichetta, config dir, versione locale e
  remota, data dell'exe, PID, uptime, file di impostazioni attivo e toggle di
  autostart; per riga: Riavvia / Ferma / Avvia / Impostazioni / Cartella e,
  quando serve, Aggiorna. «Impostazioni» apre la pagina omonima.
- **Aggiornamenti** — changelog della release reso da un renderer markdown
  inline, barra di progresso del download, log degli eventi dell'update e i
  pulsanti «Aggiorna» e «Rollback».
- **Impostazioni** — editor di `usage-monitor-settings.json` per istanza:
  modulo generato dallo schema (sezioni collassabili, ricerca, «impostata» e
  reset al default per chiave, validazione inline con le regole del monitor),
  tab JSON con diff «salvato ↔ bozza», sezione «Altre chiavi» per ciò che lo
  schema non conosce, tab «Storico» (versioni archiviate, diff, ripristino,
  esporta/importa) e barra con Salva, Salva e riavvia istanza, Ricarica, Crea
  file, Apri cartella, Copia da…, preset brand con anteprima e Testa quick
  action (con conferma).
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

### Token GitHub

Facoltativo: senza token le richieste sono anonime e GitHub concede 60
chiamate l'ora, che con quattro repo controllati ogni sei ore bastano, ma la
cache ETag e il rate limit sono piu' comodi con un token. Il valore vive nel
**Windows Credential Manager** (target `UsageMonitorManager/github`), non in
`manager-config.json` e non accanto all'eseguibile.

```powershell
# da stdin: il token non compare mai in argv, quindi nemmeno nella cronologia
umm token set

# oppure da una variabile d'ambiente gia' impostata
umm token set --from-env GITHUB_TOKEN

umm token status
umm token clear
```

```
> umm token status
token: absent (source: none)
credential target: UsageMonitorManager/github
```

L'ordine di risoluzione e' **Credential Manager -> `GITHUB_TOKEN` ->
`GH_TOKEN` -> anonimo**. La pagina «Manager» ha lo stesso comando con un campo
mascherato e mostra lo stato del rate limit; il token, una volta salvato, non
viene mai piu' mostrato ne' restituito alla pagina.

### Aggiornamento del manager

Il manager si aggiorna con lo stesso motore dei monitor, dal proprio repo. Su
Windows un eseguibile in esecuzione si puo' rinominare ma non sovrascrivere,
quindi la sequenza e': download `.download` accanto all'exe, verifica sha256,
`UsageMonitorManager.exe` -> `.bak`, `.download` -> `.exe`, avvio del nuovo
processo con `--post-update <pid vecchio>` e uscita pulita della tray. Il nuovo
processo attende la morte del vecchio (che tiene ancora il mutex singleton),
cancella il `.bak` e notifica «Manager aggiornato alla vX.Y.Z».

```
> umm self-update --check-only
nessuna release pubblicata per hybrid2102/usage-monitor-manager
```

Il repo non ha ancora release: lo stato e' `unknown`, non un errore, e la UI lo
dice cosi'. `--dry-run` elenca i passi senza toccare nulla; da sorgente
(`python -m usage_monitor_manager`) il self-update si rifiuta e suggerisce
`git pull`.

### Cronologia delle versioni e rollback

Dopo un aggiornamento riuscito l'eseguibile sostituito non sparisce: finisce in
`.umm-archive` accanto a quello nuovo, con un `manifest.json` che ne registra
versione, sha256, data e tag di release.

```
D:\OneDrive\Software\Tools\AI\Copilot\
  UsageMonitorForCopilot.exe
  .umm-archive\
    manifest.json
    UsageMonitorForCopilot-0.3.1.exe
    UsageMonitorForCopilot-0.3.0.exe
```

```
> umm archive list
KIND     VERSION  ARCHIVED             TAG      FILE
copilot  0.3.1    2026-09-18 10:42:03  v0.3.1   UsageMonitorForCopilot-0.3.1.exe
copilot  0.3.0    2026-09-02 08:11:55  v0.3.0   UsageMonitorForCopilot-0.3.0.exe
```

Si conservano le ultime tre build per eseguibile (`archive_keep` nelle
preferenze, `umm archive prune --keep N` per potare subito).
`umm rollback --kind copilot` rimette la precedente (il `.bak` se c'e' ancora,
altrimenti la piu' recente in archivio) e `--version 0.3.0` una qualsiasi:
ferma le istanze, scambia gli eseguibili — **archiviando anche quello che era
in uso**, quindi il ripristino e' a sua volta reversibile — e riavvia solo le
istanze che erano attive. Lo stesso dalla pagina «Aggiornamenti», con il
bottone «Ripristina».

`.umm-archive` e' in `.gitignore`.

### Watchdog

Ogni 30 secondi il manager confronta la fotografia dei processi con la
precedente e segnala tre anomalie:

| Anomalia | Significato | «Ripara» |
|---|---|---|
| `crashed` | l'istanza era in esecuzione e non lo e' piu' | la riavvia |
| `duplicate` | due processi app per lo stesso `(kind, suffisso)`, e i monitor sono singleton | termina i doppioni tenendo il **piu' vecchio** |
| `orphan` | coppia bootloader/app incompleta | ferma quel che resta e riavvia l'istanza |

Le anomalie compaiono nella colonna «Stato» della dashboard, nel tooltip della
tray e in `umm status`. Quando e' il manager a fermare, riavviare o aggiornare
un'istanza lo annuncia a un registro interno a scadenza, cosi' le proprie
operazioni non vengono mai scambiate per un crash. Con
`auto_restart_crashed` (default: disattivo) il riavvio e' automatico; i
duplicati non vengono **mai** terminati da soli, e comunque solo processi il
cui eseguibile coincide con quello dell'istanza.

### Aggiornamenti pianificati

`umm update --all --yes` non chiede mai nulla ed e' pensato per il Task
Scheduler: un asset di cui GitHub non pubblica lo sha256 viene **saltato**
(uscita 4) invece di essere installato alla cieca, un errore di rete esce 3 e un
fallimento parziale esce 1. `--log FILE` aggiunge una riga JSON per passo, che
e' l'unica traccia che resta quando non c'e' una console.

```powershell
schtasks /Create /TN "Usage Monitor Manager - aggiornamenti" /SC DAILY /ST 03:30 ^
  /TR "\"D:\OneDrive\Software\Tools\AI\UsageMonitorManager.exe\" update --all --yes --log \"%LOCALAPPDATA%\UsageMonitorManager\update.log\"" ^
  /RL LIMITED /F
```

```json
{"ts": "2026-09-18T03:30:11+00:00", "kind": "copilot", "exe_path": "D:\\...\\UsageMonitorForCopilot.exe", "step": "download", "message": "UsageMonitorForCopilot.exe: 15260446 of 15260446 bytes", "progress": 1.0}
{"ts": "2026-09-18T03:30:14+00:00", "kind": "copilot", "exe_path": "D:\\...\\UsageMonitorForCopilot.exe", "step": "result", "ok": true, "message": "updated to 0.3.2: version verified and every restarted instance is running", "from_version": "0.3.1", "to_version": "0.3.2"}
```

`umm status --json` e' la controparte da leggere (statusline, script): istanze,
stato aggiornamenti memorizzato e anomalie del watchdog, **senza nessuna
chiamata di rete** se non si passa `--check`.

```
> umm status --json
{
  "version": "0.7.0",
  "instances": [
    { "id": "claude", "kind": "claude", "label": "Claude", "local_version": "1.22.0", "is_running": true, "pid": 9980 },
    { "id": "claude_578560bbc610", "kind": "claude", "label": "Claude Valeria", "local_version": "1.22.0", "is_running": true, "pid": 4512 },
    { "id": "copilot", "kind": "copilot", "label": "Copilot", "local_version": "0.3.1", "is_running": true, "pid": 9588 },
    { "id": "codex", "kind": "codex", "label": "Codex", "local_version": "0.1.0", "is_running": true, "pid": 29356 }
  ],
  "running": 4,
  "updates": { "checked": false, "available": 0, "error": null },
  "self_update": null,
  "watchdog": { "last_poll": "2026-09-18T12:13:12+00:00", "count": 0, "events": [] }
}
```

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

**Fase 6 (Self-update e robustezza) completata** (`v0.7.0`): il manager
aggiorna se stesso (`umm self-update`, `--post-update`), tiene il token GitHub
nel Credential Manager, conserva le build precedenti in `.umm-archive` con
ripristino di una qualsiasi di esse, sorveglia le istanze (crash, duplicati,
coppie orfane), sa saltare una versione e ha una modalità non presidiata per il
Task Scheduler (`umm update --all --yes --log`, `umm status --json`).

**Fase 5 (Editor impostazioni) completata** (`v0.6.0`): da qui **anche
`usage-monitor-config-editor` è sostituibile** — l'editor conosce tutte le
chiavi documentate dei tre monitor, le valida con le regole del monitor prima
di scrivere, conserva le chiavi sconosciute, archivia ogni salvataggio con
storico/diff/ripristino, esporta e importa profili, copia sezioni fra istanze
e genera la palette dai preset brand (`umm config …` e la pagina
«Impostazioni»).

**`usage-monitor-updater` (PowerShell) è sostituibile** dalla fase 3: confronto
versioni, changelog, download verificato, swap con `.bak`, riavvio delle
istanze e rollback sono coperti da `umm check` / `umm update` /
`umm rollback` e dalla pagina Aggiornamenti.

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

Dalla Fase 6:

- `secrets.py` — Credential Manager in ctypes, backend sostituibile, target
  iniettabile; il token non passa mai da `argv` e non viene mai ristampato;
- `self_update.py` — `check_self` (nessuna release = `unknown`, non un errore),
  `apply_self` solo su build congelata con rilancio `--post-update` e ambiente
  ripulito, `finish_post_update` che attende il vecchio pid e cancella il `.bak`;
- `archive.py` — `.umm-archive` con manifest, keep-N per eseguibile, `restore`
  che riusa `FileSwapper` e archivia a sua volta la build sostituita;
- `watchdog.py` — confronto fra snapshot, `crashed` / `duplicate` / `orphan`,
  registro «expected down» perché le operazioni del manager non passino per
  crash, riparazioni mirate;
- CLI — `umm token`, `umm self-update`, `umm archive`, `umm skip` / `umm unskip`,
  `umm rollback --version`, `umm status --json` e `umm update --all --yes` con
  i codici di uscita e il log JSON per il Task Scheduler.

Restano alla fase 7 il kind generico «exe da GitHub Releases» (§9.9), la
modalità diagnostica (§9.7), il file `status.json` scritto dalla tray (§9.11),
i profili di avvio (§9.13) e l'hotkey globale (§9.14); PLAN.md §7 tiene il
quadro.
