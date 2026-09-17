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
umm check [--kind ...] [--json]
umm update --kind copilot [--yes] [--no-restart]
umm config show|edit|validate|set KEY VALUE --instance ...
umm self-update [--check-only]
umm status [--json]
```

Senza argomenti, `python -m usage_monitor_manager` avvia la modalità tray.

## Stato

**Fase 2 (Controllo processi) completata** (`v0.3.0`). Oltre allo scheletro
della Fase 0 (`kinds.py`, `model.py`, `identity.py`, tooling, CI) sono reali e
testati:

- `platform/` — dispatcher su `sys.platform`; `win32.py` implementa la lettura
  della versione PE (`ProductVersion` / `FileVersion` / `VS_FIXEDFILEINFO` via
  `version.dll` in ctypes), il `ProductName`, le voci di `HKCU\...\Run` (winreg)
  con parsing dei comandi quotati, la lettura in blocco degli shortcut `.lnk`
  (una sola chiamata a `WScript.Shell`), i flag Popen senza finestra e la sonda
  del mutex singleton (`mutex_is_held`: `CreateMutexW` +
  `ERROR_ALREADY_EXISTS`, handle chiuso subito);
  `linux.py` resta uno stub documentato;
- `versions.py` — parsing tollerante (`v1.22.0`, `1.22.0.0` → `1.22.0`),
  confronto e stati `up_to_date` / `update_available` / `newer_local` /
  `unknown`;
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
- `umm list [--json]` — tabella leggibile (con fallback ASCII sulle console
  cp1252) o dump JSON di `Instance.to_dict()`;
- `umm restart|stop|start` — selettori `--all`, `--kind` (ripetibile),
  `--config-dir` (ripetibile), `--id KIND:SUFFIX`, più `--json` e `--dry-run`;
  uscita 0 se tutto è andato bene, 1 se un'operazione è fallita, 3 se nessuna
  istanza corrisponde ai selettori.

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

Esempio su una macchina con quattro istanze attive:

```
KIND     CONFIG DIR                      VERSION   RUN  PIDS         AUTO  SOURCES
Claude   C:\Users\Marco\.claude          1.22.0.0  ●    32472/32520  Y     process,run_key,settings_file
Claude   C:\Users\Marco\.claude-valeria  1.22.0.0  ●    82104/93020  Y     process,run_key,shortcut,settings_file
Copilot  C:\Users\Marco\.copilot         0.3.1.0   ●    32440/17540  Y     process,run_key,settings_file
Codex    C:\Users\Marco\.codex           0.1.0.0   ●    83312/18204  Y     process,run_key,settings_file
```

E l'anteprima di un riavvio, che non tocca nulla:

```
> umm restart --dry-run --all
would stop claude pids 63192 then wait for the UsageMonitorForClaude_SingleInstance mutex
would start claude: D:\...\UsageMonitorForClaude.exe (without CLAUDE_CONFIG_DIR)
would stop claude_578560bbc610 pids 98936 then wait for the UsageMonitorForClaude_SingleInstance_578560bbc610 mutex
would start claude_578560bbc610: D:\...\UsageMonitorForClaude.exe --config-dir=C:\Users\Marco\.claude-valeria CLAUDE_CONFIG_DIR=C:\Users\Marco\.claude-valeria
```

I moduli delle fasi successive (update engine, tray/dashboard, editor
impostazioni, self-update) sono ancora stub con docstring di responsabilità e
sono descritti in PLAN.md §7.
