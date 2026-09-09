# usage-monitor-manager

Tray app in background che unifica in un'unica codebase Python:

- **config** — gestione della configurazione delle istanze monitorate (equivalente riscritto di [usage-monitor-config-editor](https://github.com/hybrid2102/usage-monitor-config-editor))
- **updater** — controllo e applicazione aggiornamenti per istanza, dal repo GitHub corretto (equivalente riscritto di [usage-monitor-updater](https://github.com/hybrid2102/usage-monitor-updater))
- **process control** — avvio, stop e riavvio manuale delle istanze configurate

## Ambito

Gestisce 4 istanze locali:

- 2x [usage-monitor-for-claude](https://github.com/jens-duttke/usage-monitor-for-claude) (jens-duttke), una per account Anthropic
- 1x [usage-monitor-for-copilot](https://github.com/hybrid2102/usage-monitor-for-copilot)
- 1x [usage-monitor-for-codex](https://github.com/hybrid2102/usage-monitor-for-codex)

Nessun autostart al login: controllo solo manuale dalla tray icon.

## Stato

In sviluppo iniziale — scheletro del progetto.

## Sviluppo

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
python -m usage_monitor_manager
```
