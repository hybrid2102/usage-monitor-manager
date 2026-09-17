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

**Fase 1 (Discovery + versioni) completata** (`v0.2.0`). Oltre allo scheletro
della Fase 0 (`kinds.py`, `model.py`, `identity.py`, tooling, CI) sono reali e
testati:

- `platform/` — dispatcher su `sys.platform`; `win32.py` implementa la lettura
  della versione PE (`ProductVersion` / `FileVersion` / `VS_FIXEDFILEINFO` via
  `version.dll` in ctypes), il `ProductName`, le voci di `HKCU\...\Run` (winreg)
  con parsing dei comandi quotati, la lettura in blocco degli shortcut `.lnk`
  (una sola chiamata a `WScript.Shell`) e i flag Popen senza finestra;
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
- `umm list [--json]` — tabella leggibile o dump JSON di `Instance.to_dict()`.

Esempio su una macchina con quattro istanze attive:

```
KIND     CONFIG DIR                      VERSION   RUN  PIDS         AUTO  SOURCES
Claude   C:\Users\Marco\.claude          1.22.0.0  ●    32472/32520  Y     process,run_key,settings_file
Claude   C:\Users\Marco\.claude-valeria  1.22.0.0  ●    82104/93020  Y     process,run_key,shortcut,settings_file
Copilot  C:\Users\Marco\.copilot         0.3.1.0   ●    32440/17540  Y     process,run_key,settings_file
Codex    C:\Users\Marco\.codex           0.1.0.0   ●    83312/18204  Y     process,run_key,settings_file
```

I moduli delle fasi successive (controllo processi, update engine,
tray/dashboard, editor impostazioni, self-update) sono ancora stub con
docstring di responsabilità e sono descritti in PLAN.md §7.
