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
| 7 | Kind `github-exe`, modalità diagnostica, `status.json`, profili di avvio, hotkey globale | 0.8.0 | (vedi sotto) |

## Fase 7 — completata

Codice: `27928fa` (wip). Test: `2107b0f`, `6d42d36`, `8e6f965`, `28a97ed`.
Documentazione e versione 0.8.0: commit corrente.

Gate: ruff, ruff format, pyright strict, **813 test** verdi.

Contenuto:
- kind generico `github-exe` (famiglia, `custom_kinds` in manager-config, `umm kind list|add|remove`, form nella pagina Manager); built-in `agent-monitor-claude` per `AgentMonitorForClaude.exe`;
- modalità diagnostica (`diag.py`, `umm diag start|stop|show`, tab Diagnostica, `manager.log` rotante 1 MB × 3);
- file di stato (`status_file.py`, `umm status --write-file`, preferenza `status_file` riscritta dalla tray, `docs/status-file.md`);
- profili di avvio (`profiles.py`, `umm profile ...`, submenu tray, editor nella pagina Manager);
- hotkey globale (`HotkeyListener` ctypes, preferenza `hotkey`, campo con «cattura»);
- test: `tests/test_kinds_custom.py`, `test_discovery_github_exe.py`, `test_diag.py`, `test_status_file.py`, `test_profiles.py`, `test_hotkey.py`, `test_cli_phase7.py`, più la fixture `pristine_kind_registry` in `conftest.py`;
- documentazione: `docs/status-file.md`, voce CHANGELOG 0.8.0, sezioni README (kind, diagnostica, status file, profili, hotkey), `__version__` = 0.8.0.

Resta da fare solo dall'utente: **prova manuale della tray e della finestra** (le fasi 5–7 sono verificate a livello API/test, non cliccando).

## Fase 8 — in corso

Ordine dei passi (uno per commit, gate verdi e questo file aggiornato a ogni passo):

- [ ] B1 i18n: `i18n.py` con `T[key]` su `locale/it.json` e `locale/en.json`, lingua da `preferences.language` (`it` default, `auto` → locale di sistema), stringhe di CLI/tray/notifiche/finestra nei cataloghi (il JS le riceve da `JsApi.get_translations()`), test che i due cataloghi abbiano le stesse chiavi e che ogni chiave usata nei `.py`/`.js` esista, `locale/*.json` nei datas dello spec PyInstaller;
- [ ] B2 log ed errori: `errors.py` con la gerarchia `UmmError(code, message, hint)`, mappatura CLI sui codici di uscita documentati (nessun traceback senza `--debug`), tray/finestra che mostrano il messaggio, ogni eccezione inattesa su `manager.log` con traceback;
- [ ] B3 documentazione: `docs/user-guide.md` (italiano), `docs/architecture.md`, README ridotto a panoramica + link;
- [ ] B4 stub Linux (§9.12) documentato + cosa servirebbe per una porta, in `docs/architecture.md`;
- [ ] B5 release: build, verifica `--version`, bump a **1.0.0** con CHANGELOG consolidato, rebuild, verifica ProductVersion del PE, README di dismissione dei due repo vecchi in `docs/deprecation/` (preparati, non applicati). Tag, push e release restano all'utente.

## Regole per chi riprende

- Ogni brief a un subagente deve vietare: stop/start/restart/update delle istanze reali, scrittura dei settings reali, della chiave Run reale e di token reali; solo `--dry-run` e comandi read-only sul reale; test con discovery, process control e rete finti.
- Motivo: nella fase 2 un agente ha riavviato le istanze reali ereditando `CLAUDE_CONFIG_DIR` e variabili `_PYI_*` dalla shell della sessione.
