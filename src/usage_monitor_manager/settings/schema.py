"""Declarative settings schema for the three monitor kinds.

One :class:`Field` per documented key of ``usage-monitor-settings.json``. The
content is a transcription of the three ``docs/configuration.md`` (Claude,
Copilot, Codex) plus the type rules enforced by the monitors' own
``settings._validate()``; :mod:`usage_monitor_manager.settings.store` reads the
schema, never the other way round.

Everything here is data: no I/O, no validation logic, no UI. The UI renders
the fields of ``schema_for(kind)`` grouped by :attr:`Schema.section_order`,
the CLI uses the same objects to describe a key, and the tests assert that
every key of the upstream docs is present for the right kinds.

Help texts are short Italian summaries of the upstream documentation, because
the manager's UI is Italian (PLAN.md section 6.6).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

__all__ = [
    "FIELD_TYPES",
    "LANGUAGES",
    "SECTION_LABELS",
    "SECTION_ORDER",
    "THRESHOLD_PREFIX",
    "Field",
    "Schema",
    "schema_for",
]

THRESHOLD_PREFIX = "alert_thresholds_"
"""Prefix of the ``alert_thresholds_<field>`` family (a key per quota field)."""

LANGUAGES = (
    "de",
    "en",
    "es",
    "fr",
    "hi",
    "id",
    "it",
    "ja",
    "ko",
    "pt-BR",
    "uk",
    "zh-CN",
    "zh-TW",
)
"""The 13 locales the monitors ship, as listed in ``docs/configuration.md``."""

FIELD_TYPES = (
    "int",
    "bool",
    "percent",
    "hex_color",
    "rgba_dict",
    "string",
    "enum",
    "string_list",
    "wildcard_list",
    "command",
    "threshold_list",
    "amount_list",
    "cli_command_map",
    "icon_fields",
    "prefix_family",
)
"""Every editor type. ``percent`` is a number in 1-100 (``alert_time_aware_below``)."""

SECTION_ORDER = (
    "alert",
    "update_notification",
    "cli",
    "tooltip",
    "popup",
    "compact",
    "icon",
    "commands",
    "polling",
    "language",
    "time",
    "currency",
    "icon_colors",
    "popup_colors",
)
"""Section identifiers in the order of the upstream documentation."""

SECTION_LABELS: dict[str, str] = {
    "alert": "Soglie di avviso",
    "update_notification": "Notifica aggiornamenti",
    "cli": "Comandi CLI",
    "tooltip": "Campi del tooltip",
    "popup": "Campi del popup",
    "compact": "Vista compatta",
    "icon": "Barre dell'icona",
    "commands": "Comandi su evento",
    "polling": "Intervalli di polling",
    "language": "Lingua",
    "time": "Formato orario",
    "currency": "Valuta",
    "icon_colors": "Colori dell'icona",
    "popup_colors": "Colori del popup",
}

RGBA_SUBKEYS = ("fg", "fg_half", "fg_dim", "fg_warn")
"""The four channels of ``icon_light`` / ``icon_dark``."""

ICON_BAR_MODES = ("utilization", "overage")
"""Suffixes accepted after ``:`` in an ``icon_fields`` entry."""

_ALL = ("claude", "copilot", "codex")
_CLAUDE_CODEX = ("claude", "codex")

# Known quota field names per kind (docs "Known field names"), used as
# suggestions by the UI and by the alert_thresholds_* prefix family.
QUOTA_FIELDS: dict[str, tuple[str, ...]] = {
    "claude": (
        "five_hour",
        "seven_day",
        "seven_day_sonnet",
        "seven_day_opus",
        "seven_day_cowork",
        "seven_day_oauth_apps",
        "extra_usage",
    ),
    "copilot": ("chat", "completions", "premium_interactions"),
    "codex": ("five_hour", "seven_day", "one_hour_code_review", "extra_usage"),
}

# Section keys accepted by compact_hide, per kind (docs "Section keys").
COMPACT_SECTIONS: dict[str, tuple[str, ...]] = {
    "claude": ("account", "extra_usage", "claude_code", "status"),
    "copilot": ("copilot_code", "status"),
    "codex": ("account", "extra_usage", "codex_code", "status"),
}


@dataclass(frozen=True, slots=True)
class Field:
    """One settings key, as the editor and the validator see it."""

    key: str
    type: str
    section: str
    default: Any
    help: str
    applies_to: tuple[str, ...] = _ALL
    minimum: int | None = None
    """Lower bound of an ``int`` field (the monitors' ``_NUMERIC_BOUNDS``)."""
    choices: tuple[str, ...] = ()
    """Allowed values of an ``enum``."""
    suggestions: tuple[str, ...] = ()
    """Non-binding completions offered by the UI (quota fields, sections...)."""
    alias_of: str | None = None
    """Set on ``on_double_click_command``: the key that supersedes it."""
    deprecated: bool = False
    subkeys: tuple[str, ...] = ()
    """Channels of an ``rgba_dict``."""
    max_entries: int | None = None
    min_entries: int | None = None

    @property
    def is_prefix_family(self) -> bool:
        return self.type == "prefix_family"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready description, consumed by the window's form builder."""
        return {
            "key": self.key,
            "type": self.type,
            "section": self.section,
            "default": self.default,
            "help": self.help,
            "applies_to": list(self.applies_to),
            "minimum": self.minimum,
            "choices": list(self.choices),
            "suggestions": list(self.suggestions),
            "alias_of": self.alias_of,
            "deprecated": self.deprecated,
            "subkeys": list(self.subkeys),
            "max_entries": self.max_entries,
            "min_entries": self.min_entries,
        }


@dataclass(frozen=True, slots=True)
class Schema:
    """The fields of one kind, in documentation order."""

    kind: str
    fields: Mapping[str, Field]
    section_order: tuple[str, ...] = SECTION_ORDER
    section_labels: Mapping[str, str] = dataclass_field(default_factory=lambda: dict(SECTION_LABELS))

    def keys(self) -> tuple[str, ...]:
        """Every key of the schema, in documentation order."""
        return tuple(self.fields)

    def __contains__(self, key: object) -> bool:
        return self.field_for(str(key)) is not None

    def __iter__(self) -> Iterator[Field]:
        return iter(self.fields.values())

    def field_for(self, key: str) -> Field | None:
        """The field describing ``key``, resolving the prefix family.

        ``alert_thresholds_seven_day_opus`` is not listed explicitly but is a
        valid key: it resolves to the ``alert_thresholds_`` family field.
        """
        explicit = self.fields.get(key)
        if explicit is not None:
            return explicit
        for candidate in self.fields.values():
            if candidate.is_prefix_family and key.startswith(candidate.key) and key != candidate.key:
                return candidate
        return None

    def default_for(self, key: str) -> Any:
        """The upstream default of ``key`` (``None`` when unknown)."""
        found = self.fields.get(key)
        return None if found is None else found.default

    def sections(self) -> list[tuple[str, str, list[Field]]]:
        """``(id, label, fields)`` per non-empty section, in order."""
        grouped: dict[str, list[Field]] = {section: [] for section in self.section_order}
        for entry in self.fields.values():
            grouped.setdefault(entry.section, []).append(entry)
        return [
            (section, self.section_labels.get(section, section), grouped[section])
            for section in self.section_order
            if grouped.get(section)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "section_order": list(self.section_order),
            "section_labels": dict(self.section_labels),
            "fields": [entry.to_dict() for entry in self.fields.values()],
            "quota_fields": list(QUOTA_FIELDS.get(self.kind, ())),
            "compact_sections": list(COMPACT_SECTIONS.get(self.kind, ())),
            "threshold_prefix": THRESHOLD_PREFIX,
            "rgba_subkeys": list(RGBA_SUBKEYS),
            "icon_bar_modes": list(ICON_BAR_MODES),
        }


# --------------------------------------------------------------------------- #
# The field table
# --------------------------------------------------------------------------- #


def _thresholds(kind: str) -> list[Field]:
    """``alert_thresholds_*`` plus the family field, with the kind's defaults."""
    if kind == "copilot":
        explicit = [
            Field(
                key="alert_thresholds_premium_interactions",
                type="threshold_list",
                section="alert",
                default=[50, 80, 95],
                help="Soglie (%) per la quota premium_interactions. Lista vuota = nessun avviso.",
                applies_to=("copilot",),
            )
        ]
    else:
        explicit = [
            Field(
                key="alert_thresholds_five_hour",
                type="threshold_list",
                section="alert",
                default=[50, 80, 95],
                help="Soglie (%) per la sessione da 5 ore. Lista vuota = nessun avviso.",
                applies_to=(kind,),
            ),
            Field(
                key="alert_thresholds_seven_day",
                type="threshold_list",
                section="alert",
                default=[95],
                help="Soglie (%) per le quote settimanali (7 giorni e tutte le varianti).",
                applies_to=(kind,),
            ),
            Field(
                key="alert_thresholds_extra_usage",
                type="threshold_list",
                section="alert",
                default=[50, 80, 95],
                help="Soglie (%) per l'uso extra a pagamento (overage).",
                applies_to=(kind,),
            ),
        ]
    explicit.append(
        Field(
            key=THRESHOLD_PREFIX,
            type="prefix_family",
            section="alert",
            default=[],
            help=(
                "Famiglia alert_thresholds_<campo>: soglie (%) per una quota specifica. "
                "La ricerca usa prima la chiave esatta, poi il periodo base."
            ),
            applies_to=(kind,),
            suggestions=tuple(f"{THRESHOLD_PREFIX}{name}" for name in QUOTA_FIELDS[kind]),
        )
    )
    return explicit


def _icon_fields_field(kind: str) -> Field:
    if kind == "copilot":
        return Field(
            key="icon_fields",
            type="icon_fields",
            section="icon",
            default=["premium_interactions"],
            help=(
                "Campi mostrati come barre nell'icona (1 o 2). Il primo determina il testo; "
                "si può aggiungere il suffisso ':utilization' o ':overage'."
            ),
            applies_to=("copilot",),
            suggestions=QUOTA_FIELDS["copilot"],
            min_entries=1,
            max_entries=2,
        )
    return Field(
        key="icon_fields",
        type="icon_fields",
        section="icon",
        default=["five_hour", "seven_day"],
        help=(
            "I due campi mostrati come barre nell'icona: il primo è la barra superiore "
            "(e il testo dell'icona), il secondo quella inferiore. Suffisso ':utilization' o ':overage'."
        ),
        applies_to=(kind,),
        suggestions=QUOTA_FIELDS[kind],
        min_entries=2,
        max_entries=2,
    )


def _notify_field(kind: str) -> Field:
    key = f"notify_{kind}_update"
    if kind == "claude":
        help_text = "Notifica quando un refresh del token in background installa una nuova versione della CLI Claude."
    else:
        help_text = (
            f"Notifica l'aggiornamento della CLI {kind.capitalize()} "
            "(chiave di compatibilità ereditata dal monitor originale)."
        )
    return Field(
        key=key,
        type="bool",
        section="update_notification",
        default=True,
        help=help_text,
        applies_to=(kind,),
    )


_COMMAND_HELP = {
    "on_reset_command": "Comando (stringa o array) eseguito quando una quota si azzera.",
    "on_startup_command": "Comando eseguito una volta dopo il primo aggiornamento API riuscito.",
    "on_threshold_command": "Comando eseguito quando l'uso supera una soglia configurata.",
    "quick_action_command": ("Comando della quick action: doppio clic sull'icona o voce «Run Quick Action» del menu."),
}

_POLL_FIELDS: tuple[tuple[str, int, int, str], ...] = (
    ("poll_interval", 180, 1, "Secondi fra due aggiornamenti dall'API."),
    ("poll_fast", 120, 1, "Secondi quando l'uso sta crescendo."),
    ("poll_fast_extra", 2, 1, "Polling veloci aggiuntivi dopo che l'uso smette di crescere."),
    ("poll_error", 30, 1, "Secondi dopo un errore transitorio (5xx, rete). Il 429 usa il backoff."),
    ("max_backoff", 900, 1, "Backoff massimo in secondi per gli errori di rate limit (15 min)."),
    ("idle_pause", 300, 0, "Secondi di inattività prima di rallentare a idle_interval (0 = disattiva)."),
    ("idle_interval", 900, 1, "Secondi fra due aggiornamenti quando nessuno usa la macchina (15 min)."),
)

_POPUP_COLORS: tuple[tuple[str, str, str], ...] = (
    ("bg", "#1e1e1e", "Sfondo del popup."),
    ("fg", "#cccccc", "Testo."),
    ("fg_dim", "#888888", "Testo attenuato (etichette, orari di reset)."),
    ("fg_heading", "#ffffff", "Titoli di sezione."),
    ("fg_link", "#4a9eff", "Testo dei link (es. changelog)."),
    ("bar_bg", "#333333", "Sfondo delle barre di avanzamento."),
    ("bar_fg", "#4a9eff", "Riempimento delle barre."),
    ("bar_fg_warn", "#e05050", "Riempimento delle barre in allarme e testo di errore."),
    ("bar_divider", "#000c", "Divisori temporali sulle barre."),
    ("bar_marker", "#fffc", "Marcatore della posizione temporale sulle barre."),
)


def _build(kind: str) -> dict[str, Field]:
    quota = QUOTA_FIELDS[kind]
    list_suggestions = quota
    compact_suggestions = COMPACT_SECTIONS[kind] + quota
    fields: list[Field] = []

    fields.extend(_thresholds(kind))
    if kind in _CLAUDE_CODEX:
        fields.extend(
            [
                Field(
                    key="alert_extra_usage_spent",
                    type="amount_list",
                    section="alert",
                    default=[],
                    help=(
                        "Importi assoluti di spesa extra (valuta di fatturazione) che fanno scattare una "
                        "notifica: l'unico avviso che funziona senza limite mensile."
                    ),
                    applies_to=_CLAUDE_CODEX,
                ),
                Field(
                    key="alert_time_aware",
                    type="bool",
                    section="alert",
                    default=True,
                    help="Avvisa solo quando l'uso corre più in fretta del tempo trascorso.",
                    applies_to=_CLAUDE_CODEX,
                ),
                Field(
                    key="alert_time_aware_below",
                    type="percent",
                    section="alert",
                    default=90,
                    help=(
                        "Il controllo time-aware si applica solo alle soglie sotto questo valore; "
                        "quelle uguali o superiori scattano sempre."
                    ),
                    applies_to=_CLAUDE_CODEX,
                ),
            ]
        )

    fields.append(_notify_field(kind))

    fields.append(
        Field(
            key="cli_command",
            type="cli_command_map",
            section="cli",
            default={},
            help=(
                "Oggetto nome → comando (array di stringhe) di cui riportare la versione, "
                "in aggiunta a quelli rilevati automaticamente (es. un'installazione in WSL)."
            ),
            applies_to=(kind,),
        )
    )

    fields.append(
        Field(
            key="tooltip_fields",
            type="string_list",
            section="tooltip",
            default=["premium_interactions"] if kind == "copilot" else ["five_hour", "seven_day"],
            help="Campi di utilizzo mostrati nel tooltip della tray, in ordine. I duplicati vengono rimossi.",
            applies_to=(kind,),
            suggestions=list_suggestions,
        )
    )
    fields.append(
        Field(
            key="popup_fields",
            type="wildcard_list",
            section="popup",
            default=["*"],
            help=(
                "Campi mostrati nel popup, in ordine. «*» significa «tutti i restanti» "
                "e può comparire al massimo una volta."
            ),
            applies_to=(kind,),
            suggestions=("*", *list_suggestions),
        )
    )
    fields.append(
        Field(
            key="compact_hide",
            type="string_list",
            section="compact",
            default=[],
            help="Sezioni e barre da nascondere mentre il popup è appuntato (vista compatta).",
            applies_to=(kind,),
            suggestions=compact_suggestions,
        )
    )

    fields.append(_icon_fields_field(kind))
    fields.append(
        Field(
            key="icon_style",
            type="enum",
            section="icon",
            default="number+bars",
            help=(
                "Layout dell'icona: «number+bars» percentuale sopra le barre, "
                "«numbers» due percentuali impilate senza barre."
            ),
            applies_to=(kind,),
            choices=("number+bars", "numbers"),
        )
    )

    for key, help_text in _COMMAND_HELP.items():
        fields.append(
            Field(
                key=key,
                type="command",
                section="commands",
                default=[],
                help=help_text,
                applies_to=(kind,),
            )
        )
    fields.append(
        Field(
            key="on_double_click_command",
            type="command",
            section="commands",
            default=[],
            help="Nome storico di quick_action_command: funziona ancora, ma la chiave nuova ha la precedenza.",
            applies_to=(kind,),
            alias_of="quick_action_command",
            deprecated=True,
        )
    )

    for key, default, minimum, help_text in _POLL_FIELDS:
        fields.append(
            Field(
                key=key,
                type="int",
                section="polling",
                default=default,
                help=help_text,
                applies_to=(kind,),
                minimum=minimum,
            )
        )

    fields.append(
        Field(
            key="language",
            type="enum",
            section="language",
            default="",
            help="Forza la lingua dell'interfaccia del monitor; vuoto = rilevata dal sistema.",
            applies_to=(kind,),
            choices=("", *LANGUAGES),
        )
    )
    fields.append(
        Field(
            key="time_format",
            type="enum",
            section="time",
            default="24h",
            help="Formato dell'orario di reset: «24h» (14:30) o «12h» (2:30 PM).",
            applies_to=(kind,),
            choices=("24h", "12h"),
        )
    )
    if kind in _CLAUDE_CODEX:
        fields.append(
            Field(
                key="currency_symbol",
                type="string",
                section="currency",
                default="",
                help="Forza il simbolo di valuta mostrato (es. «$», «€»); vuoto = dall'API o dal sistema.",
                applies_to=_CLAUDE_CODEX,
            )
        )

    fields.append(
        Field(
            key="icon_light",
            type="rgba_dict",
            section="icon_colors",
            default={
                "fg": [255, 255, 255, 255],
                "fg_half": [255, 255, 255, 80],
                "fg_dim": [255, 255, 255, 140],
                "fg_warn": [224, 80, 80, 255],
            },
            help="Icona chiara per barra delle applicazioni scura: canali RGBA [R, G, B, A] 0-255.",
            applies_to=(kind,),
            subkeys=RGBA_SUBKEYS,
        )
    )
    fields.append(
        Field(
            key="icon_dark",
            type="rgba_dict",
            section="icon_colors",
            default={
                "fg": [0, 0, 0, 255],
                "fg_half": [0, 0, 0, 80],
                "fg_dim": [0, 0, 0, 140],
                "fg_warn": [224, 80, 80, 255],
            },
            help="Icona scura per barra delle applicazioni chiara: canali RGBA [R, G, B, A] 0-255.",
            applies_to=(kind,),
            subkeys=RGBA_SUBKEYS,
        )
    )

    for key, default_color, help_text in _POPUP_COLORS:
        fields.append(
            Field(
                key=key,
                type="hex_color",
                section="popup_colors",
                default=default_color,
                help=help_text,
                applies_to=(kind,),
            )
        )

    return {entry.key: entry for entry in fields}


_SCHEMAS: dict[str, Schema] = {}


def schema_for(kind: str) -> Schema:
    """The schema of one kind (``claude``, ``copilot``, ``codex``).

    Raises:
        KeyError: if the kind is not one of the three monitors.
    """
    key = kind.strip().casefold()
    if key not in QUOTA_FIELDS:
        known = ", ".join(sorted(QUOTA_FIELDS))
        raise KeyError(f"unknown monitor kind {kind!r} (known: {known})")
    cached = _SCHEMAS.get(key)
    if cached is None:
        cached = Schema(kind=key, fields=_build(key))
        _SCHEMAS[key] = cached
    return cached
