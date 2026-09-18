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

- [**Guida utente**](docs/user-guide.md) — installazione, primo avvio e import
  della configurazione precedente, tray, pagine della finestra, aggiornamenti e
  rollback, editor delle impostazioni, profili, diagnostica, token, autostart,
  aggiornamento pianificato, risoluzione dei problemi.
- [**Architettura**](docs/architecture.md) — mappa dei moduli come sono stati
  costruiti, invarianti di sicurezza, unico percorso di avvio, cosa servirebbe
  per una porta Linux.
- [**`status.json`**](docs/status-file.md) — schema del file di stato ed esempi
  per statusline e script.
- [PLAN.md](PLAN.md) — piano di sviluppo completo: ricognizione, decisioni
  architetturali, design dei moduli, fasi. [STATUS.md](STATUS.md) — stato di
  avanzamento.

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
umm status [--json] [--check] [--write-file [PERCORSO]]
umm kind list|add KEY --exe-name FILE --repo OWNER/NAME|remove KEY [--json]
umm diag start|stop|show [--instance ID] [--lines N] [--dry-run] [--json]
umm profile list|add NAME --instance ID|remove NAME|show NAME|apply NAME [--yes] [--dry-run] [--no-stop-others]
umm tray
umm import-legacy [--dry-run] [--json]
umm autostart --manager [on|off|status] [--json]
umm autostart --instance ID [on|off|status] [--json]
```

Codici di uscita: `0` tutto bene, `1` almeno un'operazione fallita, `2` comando
non implementato, `3` nessuna istanza corrispondente / errore di rete, `4`
aggiornamento saltato perche' l'asset non e' verificabile, `10` (`umm check`) ci
sono aggiornamenti. Un errore stampa il messaggio e, quando c'e', il
suggerimento; `--debug` mostra il traceback invece della riga sola.

Lingua dell'interfaccia e della CLI: `preferences.language` (`it` per default,
`en`, oppure `auto` per seguire il locale di sistema).

Senza argomenti, `python -m usage_monitor_manager` avvia la modalità tray
(`umm tray` fa lo stesso; `--tray`, la forma scritta nella Run key secondo
PLAN.md §6.5, è accettata come alias).

## Build

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

**Fase 8 (Rifinitura) completata**: interfaccia e CLI tradotte (it/en,
`preferences.language`, `auto` segue il locale di sistema), gerarchia di errori
unica con i codici di uscita documentati, log su `manager.log`, documentazione
utente e di architettura, stub Linux documentato.

Le fasi precedenti: 0 scheletro, 1 discovery e versioni, 2 controllo processi,
3 update engine (da qui `usage-monitor-updater` è sostituibile), 4 tray e
finestra, 5 editor delle impostazioni (da qui lo è anche
`usage-monitor-config-editor`), 6 self-update e robustezza, 7 kind
`github-exe`, diagnostica, `status.json`, profili e hotkey. Il dettaglio di
ogni versione è in [CHANGELOG.md](CHANGELOG.md); il quadro delle fasi in
[PLAN.md](PLAN.md) §7.
