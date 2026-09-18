# Stato di avanzamento

Aggiornato: 2026-09-18. Riferimento: [PLAN.md](PLAN.md) §7.

## Fasi completate (tutto su `main`, nulla pushato, nessun tag)

| Fase | Contenuto | Versione | Ultimo commit |
|---|---|---|---|
| 0 | Layout, kind registry, modello, tooling, CI | 0.1.0 | `5c29c73` |
| 1 | Discovery, versioni PE, `umm list` | 0.2.0 | `aae9905` |
| 2 | Start/stop/restart, pulizia ambiente figli | 0.3.0 | `4465487` |
| 3 | Update engine, `umm check|update|rollback` | 0.4.0 | `c05384d` |
| 4 | Tray, finestra, scheduler, import legacy, autostart, build/release | 0.5.0 | `d0a7501` |
| 5 | Editor impostazioni, storico/diff, preset, `umm config` | 0.6.0 | `49301cb` |
| 6 | Self-update, token in Credential Manager, archivio/rollback, prerelease/skip, watchdog, update non interattivo | 0.7.0 | `62bdbf9` |

## Fase 7 — parziale (commit `27928fa`, `wip(phase-7)`)

Gate verdi: ruff, pyright strict, 623 test (nessun test nuovo per la fase 7).

Fatto:
- kind generico `github-exe` (famiglia, `custom_kinds` in manager-config, `umm kind list|add|remove`, form nella pagina Manager); built-in `agent-monitor-claude` per `AgentMonitorForClaude.exe` (fixture release registrata);
- modalità diagnostica (`diag.py`, `umm diag start|stop|show`, tab Diagnostica, `manager.log` rotante);
- file di stato (`status_file.py`, `umm status --write-file`, preferenza `status_file` riscritta dalla tray);
- profili di avvio (`profiles.py`, `umm profile ...`, submenu tray, editor nella pagina Manager);
- hotkey globale (`HotkeyListener` ctypes, preferenza `hotkey`, campo con "cattura").

Test della fase 7 (in corso, un passo per commit):
- [x] A1a kind custom (merge/validazione/persistenza) + discovery `github-exe` (coppia, dormiente, nessun `environ()`) + piano di update e archivio: `tests/test_kinds_custom.py`, `tests/test_discovery_github_exe.py`, fixture `pristine_kind_registry` in `conftest.py` (663 test);
- [x] A1b diag (argv `--verbose`, log path sotto `logs/`, stop torna in modalita normale, stop fallito non lancia nulla, `diag.json` corrotto) + `manager.log` rotante 1 MB x 3 + status file (schema completo, scrittura atomica, `generated_at`, errore GitHub nel campo `error`): `tests/test_diag.py`, `tests/test_status_file.py` (702 test);
- [x] A1c profili (piano a tre insiemi, ferma solo la famiglia usage-monitor, gap fra due start, membri sconosciuti) + hotkey (tabelle parse/format/normalize, ciclo completo del listener con `user32` finto, conflitto 1409): `tests/test_profiles.py`, `tests/test_hotkey.py` (776 test);
- [ ] A1d CLI `kind|diag|profile|status --write-file`.

Da fare per chiudere la fase 7:
- `docs/status-file.md`, voce CHANGELOG 0.8.0, README (kinds, diag, status file, profili, hotkey);
- bump `__version__` a 0.8.0;
- prova manuale della tray e della finestra (le fasi 5–7 sono verificate solo a livello API/test).

## Fase 8 — non iniziata

i18n it/en, log su file e gestione errori uniforme, doc utente, stub `platform/linux.py` documentato, poi README di rimando e archiviazione dei repo `usage-monitor-updater` e `usage-monitor-config-editor` dopo il tag 1.0.

## Regole per chi riprende

- Ogni brief a un subagente deve vietare: stop/start/restart/update delle istanze reali, scrittura dei settings reali, della chiave Run reale e di token reali; solo `--dry-run` e comandi read-only sul reale; test con discovery, process control e rete finti.
- Motivo: nella fase 2 un agente ha riavviato le istanze reali ereditando `CLAUDE_CONFIG_DIR` e variabili `_PYI_*` dalla shell della sessione.
