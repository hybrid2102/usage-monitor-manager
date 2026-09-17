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

**Fase 0 (Reset) completata.** Il repo contiene lo scheletro compilabile del
nuovo package: layout definitivo, `pyproject.toml`, ruff/pyright/pytest/
pre-commit, CI su `windows-latest`, e le prime parti reali e testate:

- `kinds.py` — registro dichiarativo dei tre kind (claude, copilot, codex);
- `model.py` — dataclass del dominio con `to_dict()` per l'output JSON;
- `identity.py` — parsing di `--config-dir` e calcolo del suffisso di istanza,
  identico all'algoritmo dei monitor (`~/.claude-valeria` → `_578560bbc610`);
- `cli.py` — superficie completa dei sottocomandi (per ora `exit 2`).

Tutti gli altri moduli sono stub con docstring di responsabilità. Le fasi
successive (discovery, controllo processi, update engine, tray/dashboard,
editor impostazioni, self-update) sono descritte in PLAN.md §7.
