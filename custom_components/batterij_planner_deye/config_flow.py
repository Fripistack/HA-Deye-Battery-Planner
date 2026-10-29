"""Config flow en options flow voor Batterij Planner Deye."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from . import deye
from .const import (
    BRON_KEYS,
    CONF_ACCU_VERMOGEN,
    CONF_DEVICE,
    CONF_EV_AAN,
    CONF_EV_DOEL_SOC_ENTITY,
    CONF_EV_MODUS,
    CONF_EV_SOC,
    CONF_EV_VERBONDEN,
    CONF_EV_VERMOGEN,
    CONF_EXPORT_BLOKKEREN,
    CONF_LADEN_AAN,
    CONF_LADEN_UIT,
    CONF_LOAD,
    CONF_MODUS_ONTLADEN,
    CONF_MODUS_ZELF,
    CONF_NETLAADSTROOM,
    CONF_NOTIFY,
    CONF_P1_AFNAME_KWH,
    CONF_P1_TERUG_KWH,
    CONF_P1_TERUG_VERMOGEN,
    CONF_P1_VERMOGEN,
    CONF_PRIJS_MORGEN,
    CONF_PRIJS_VANDAAG,
    CONF_PROG_LADEN,
    CONF_PROG_POWER,
    CONF_PROG_SOC,
    CONF_PV_MORGEN,
    CONF_PV_REST,
    CONF_PV_VERMOGEN,
    CONF_SOC,
    CONF_SOLAR_SELL,
    CONF_SOLCAST_MORGEN,
    CONF_SOLCAST_VANDAAG,
    CONF_SPANNING,
    CONF_TOU,
    CONF_TOU_AAN,
    CONF_TRAPPEN,
    CONF_WERKMODUS,
    CONF_WP_AAN,
    CONF_WP_BUFFER,
    CONF_WP_BUITEN,
    CONF_WP_MIN_MINUTEN,
    CONF_WP_SETPOINT,
    CONF_WP_THERMOSTAAT,
    DOMAIN,
    EV_ENTITY_KEYS,
    GEEN_MELDING,
    MAX_OMVORMERS,
    METING_KEYS,
    METING_MEERDERE,
    OMVORMER_MEERDERE,
    OMVORMER_VELDEN,
    STANDAARD_INSTELLINGEN,
    SUGGESTIES,
    WP_ENTITY_KEYS,
    omv_key,
)

VERPLICHT = {CONF_SOC, CONF_PRIJS_VANDAAG, CONF_PRIJS_MORGEN, CONF_P1_VERMOGEN}

DOMEIN_PER_KEY: dict[str, str | list[str]] = {
    CONF_SOC: "sensor", CONF_PRIJS_VANDAAG: "sensor", CONF_PRIJS_MORGEN: "sensor",
    CONF_PV_REST: "sensor", CONF_PV_MORGEN: "sensor",
    CONF_SOLCAST_VANDAAG: "sensor", CONF_SOLCAST_MORGEN: "sensor",
    CONF_P1_VERMOGEN: "sensor", CONF_P1_TERUG_VERMOGEN: "sensor",
    CONF_P1_AFNAME_KWH: "sensor", CONF_P1_TERUG_KWH: "sensor",
    CONF_LOAD: "sensor", CONF_PV_VERMOGEN: "sensor", CONF_ACCU_VERMOGEN: "sensor",
    CONF_PROG_SOC: "number", CONF_PROG_POWER: "number", CONF_PROG_LADEN: ["select", "switch"],
    CONF_WERKMODUS: "select", CONF_TOU: ["select", "switch"], CONF_SOLAR_SELL: "switch",
    CONF_NETLAADSTROOM: "number",
    CONF_EV_MODUS: "select", CONF_EV_VERBONDEN: "binary_sensor", CONF_EV_SOC: "sensor",
    CONF_EV_VERMOGEN: "sensor", CONF_EV_DOEL_SOC_ENTITY: ["number", "sensor"],
    CONF_WP_SETPOINT: ["number", "climate", "water_heater", "switch", "input_boolean"],
    CONF_WP_BUFFER: "sensor", CONF_WP_BUITEN: "sensor", CONF_WP_THERMOSTAAT: "climate",
}
MEERDERE = METING_MEERDERE | OMVORMER_MEERDERE


def _voorstel(hass: HomeAssistant, key: str, huidig: dict[str, Any]) -> Any:
    if key in huidig:
        return huidig.get(key)
    sug = SUGGESTIES.get(key)
    if sug and hass.states.get(sug) is not None:
        return sug
    return None


def _entity_selector(key: str) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=DOMEIN_PER_KEY[key], multiple=key in MEERDERE)
    )


def _entity_schema(hass: HomeAssistant, keys: list[str], huidig: dict[str, Any]) -> vol.Schema:
    velden: dict[Any, Any] = {}
    for key in keys:
        sel = _entity_selector(key)
        voorstel = _voorstel(hass, key, huidig)
        if key in VERPLICHT:
            if voorstel:
                velden[vol.Required(key, default=voorstel)] = sel
            else:
                velden[vol.Required(key)] = sel
        else:
            velden[vol.Optional(key, description={"suggested_value": voorstel})] = sel
    return vol.Schema(velden)


def _met_lege_optionele(keys: list[str], user_input: dict[str, Any]) -> dict[str, Any]:
    """Leeggemaakte optionele velden expliciet op None zetten."""
    return {k: user_input.get(k) for k in keys}


def _controleer_bronnen(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    fouten: dict[str, str] = {}
    soc = hass.states.get(data.get(CONF_SOC) or "")
    if soc is None:
        fouten[CONF_SOC] = "niet_gevonden"
    else:
        try:
            float(soc.state)
        except ValueError:
            fouten[CONF_SOC] = "geen_getal"
    prijs = hass.states.get(data.get(CONF_PRIJS_VANDAAG) or "")
    if prijs is None or not isinstance(prijs.attributes.get("prices"), list):
        fouten[CONF_PRIJS_VANDAAG] = "geen_prijzen"
    return fouten


def _num(minimum: float, maximum: float, stap: float | str, eenheid: str | None = None) -> selector.NumberSelector:
    # unit_of_measurement alleen meegeven als er een eenheid is: HA eist een str
    # en weigert None (dan opent het formulier niet).
    extra = {"unit_of_measurement": eenheid} if eenheid else {}
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=stap, mode=selector.NumberSelectorMode.BOX, **extra
        )
    )


def _select(opties: list, custom: bool = False, vertaling: str | None = None) -> selector.SelectSelector:
    extra = {"translation_key": vertaling} if vertaling else {}
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=opties, custom_value=custom, mode=selector.SelectSelectorMode.DROPDOWN, **extra
        )
    )


def _opties_van(hass: HomeAssistant, entity_id: Any, standaard: list[str]) -> list[str]:
    """Opties van een select-entiteit, of de standaardlijst als die er (nog) niet is."""
    if isinstance(entity_id, list):
        entity_id = entity_id[0] if entity_id else None
    st = hass.states.get(entity_id) if entity_id else None
    opties = [str(o) for o in (st.attributes.get("options") or [])] if st else []
    return opties or standaard


SECTIES: dict[str, list[str]] = {
    "accu": [
        "capaciteit_kwh", "min_soc", "max_soc", "max_laad_w", "max_ontlaad_w", CONF_TRAPPEN,
        "rendement_laden", "rendement_ontladen", "slijtage_eur_kwh", "max_net_w",
    ],
    "prijzen": ["saldering", "saldering_correctie", "teruglever_opslag", CONF_EXPORT_BLOKKEREN, "eindwaarde_factor"],
    "voorspelling": ["pv_factor", "verbruik_fallback_w"],
    "omvormer": [CONF_SPANNING, CONF_MODUS_ZELF, CONF_MODUS_ONTLADEN, CONF_LADEN_AAN, CONF_LADEN_UIT, CONF_TOU_AAN],
    "meldingen": [CONF_NOTIFY],
}


def _plat(user_input: dict[str, Any], secties: dict[str, Any]) -> dict[str, Any]:
    """Secties uit het formulier platslaan naar één dict met instellingen."""
    plat: dict[str, Any] = {}
    for key, waarde in user_input.items():
        if key in secties and isinstance(waarde, dict):
            plat.update(waarde)
        else:
            plat[key] = waarde
    return plat


def _instellingen_schema(hass: HomeAssistant, huidig: dict[str, Any]) -> vol.Schema:
    w = {**STANDAARD_INSTELLINGEN, **{k: v for k, v in huidig.items() if v is not None}}
    werkmodi = _opties_van(hass, huidig.get(omv_key(CONF_WERKMODUS, 1)),
                           ["Export First", "Zero Export To Load", "Zero Export To CT"])
    laden = _opties_van(hass, huidig.get(omv_key(CONF_PROG_LADEN, 1)), ["Disabled", "Grid", "Generator", "Both"])
    tou = _opties_van(hass, huidig.get(omv_key(CONF_TOU, 1)), ["Enabled", "Week", "Disabled"])

    notify_opties = [selector.SelectOptionDict(value=GEEN_MELDING, label="No notification")]
    for naam in sorted(hass.services.async_services_for_domain("notify")):
        notify_opties.append(selector.SelectOptionDict(value=f"notify.{naam}", label=f"notify.{naam}"))

    velden: dict[str, Any] = {
        "capaciteit_kwh": _num(1, 200, 0.1, "kWh"),
        "min_soc": _num(0, 60, 1, "%"),
        "max_soc": _num(40, 100, 1, "%"),
        "max_laad_w": _num(100, 50000, 100, "W"),
        "max_ontlaad_w": _num(100, 50000, 100, "W"),
        CONF_TRAPPEN: selector.BooleanSelector(),
        "rendement_laden": _num(70, 100, 0.5, "%"),
        "rendement_ontladen": _num(70, 100, 0.5, "%"),
        "slijtage_eur_kwh": _num(0, 0.3, 0.001, "€/kWh"),
        "max_net_w": _num(1000, 60000, 100, "W"),
        "saldering": _select(["auto", "aan", "uit"], vertaling="saldering"),
        "saldering_correctie": _num(0, 0.2, "any", "€/kWh"),
        "teruglever_opslag": _num(-0.1, 0.2, "any", "€/kWh"),
        CONF_EXPORT_BLOKKEREN: selector.BooleanSelector(),
        "eindwaarde_factor": _num(0.3, 1.5, 0.05),
        "pv_factor": _num(0.3, 1.5, 0.05),
        "verbruik_fallback_w": _num(0, 5000, 10, "W"),
        CONF_SPANNING: _num(10, 1000, 0.1, "V"),
        CONF_MODUS_ZELF: _select(werkmodi, custom=True),
        CONF_MODUS_ONTLADEN: _select(werkmodi, custom=True),
        CONF_LADEN_AAN: _select(laden, custom=True),
        CONF_LADEN_UIT: _select(laden, custom=True),
        CONF_TOU_AAN: _select(tou, custom=True),
        CONF_NOTIFY: _select(notify_opties, custom=True),
    }
    schema: dict[Any, Any] = {}
    for sectie, keys in SECTIES.items():
        sub = vol.Schema({vol.Required(k, default=w[k]): velden[k] for k in keys})
        schema[vol.Required(sectie)] = section(sub, {"collapsed": False})
    return vol.Schema(schema)


# ---------------------------------------------------------------- omvormers
def _apparaat_schema(huidig: dict[str, Any]) -> vol.Schema:
    velden: dict[Any, Any] = {}
    for nr in range(1, MAX_OMVORMERS + 1):
        key = omv_key(CONF_DEVICE, nr)
        sel = selector.DeviceSelector(selector.DeviceSelectorConfig())
        if nr == 1:
            if huidig.get(key):
                velden[vol.Required(key, default=huidig[key])] = sel
            else:
                velden[vol.Required(key)] = sel
        else:
            velden[vol.Optional(key, description={"suggested_value": huidig.get(key)})] = sel
    return vol.Schema(velden)


def _ontdek(hass: HomeAssistant, device_id: str | None) -> dict[str, Any]:
    if not device_id:
        return {}
    reg = er.async_get(hass)
    ids = [e.entity_id for e in er.async_entries_for_device(reg, device_id)]
    return deye.ontdek(ids)


def _omvormer_schema(hass: HomeAssistant, huidig: dict[str, Any]) -> vol.Schema:
    schema: dict[Any, Any] = {}
    for nr in range(1, MAX_OMVORMERS + 1):
        device = huidig.get(omv_key(CONF_DEVICE, nr))
        if nr > 1 and not device:
            continue
        gevonden = _ontdek(hass, device)
        sub: dict[Any, Any] = {}
        for veld in OMVORMER_VELDEN:
            key = omv_key(veld, nr)
            voorstel = huidig.get(key) or gevonden.get(veld)
            sub[vol.Optional(veld, description={"suggested_value": voorstel})] = _entity_selector(veld)
        schema[vol.Required(f"omvormer_{nr}")] = section(vol.Schema(sub), {"collapsed": False})
    return vol.Schema(schema)


def _omvormer_data(user_input: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for nr in range(1, MAX_OMVORMERS + 1):
        sectie = user_input.get(f"omvormer_{nr}") or {}
        for veld in OMVORMER_VELDEN:
            data[omv_key(veld, nr)] = sectie.get(veld)
    return data


def _controleer_omvormer(data: dict[str, Any]) -> dict[str, str]:
    if not data.get(omv_key(CONF_PROG_SOC, 1)) or not data.get(omv_key(CONF_WERKMODUS, 1)):
        return {"base": "omvormer_onvolledig"}
    return {}


# ---------------------------------------------------------------- autolader / warmtepomp
def _ev_schema(hass: HomeAssistant, huidig: dict[str, Any]) -> vol.Schema:
    w = {**STANDAARD_INSTELLINGEN, **{k: v for k, v in huidig.items() if v is not None}}
    modi = _opties_van(hass, huidig.get(CONF_EV_MODUS), ["off", "pv", "minpv", "now"])
    regeling = vol.Schema(
        {
            vol.Required("ev_capaciteit_kwh", default=w["ev_capaciteit_kwh"]): _num(5, 200, 1, "kWh"),
            vol.Required("ev_laadvermogen_w", default=w["ev_laadvermogen_w"]): _num(1000, 22000, 100, "W"),
            vol.Required("ev_rendement", default=w["ev_rendement"]): _num(70, 100, 1, "%"),
            vol.Required("ev_doel_soc", default=w["ev_doel_soc"]): _num(10, 100, 5, "%"),
            vol.Required("ev_vertrek", default=w["ev_vertrek"]): selector.TimeSelector(),
            vol.Required("ev_noodlaad_soc", default=w["ev_noodlaad_soc"]): _num(0, 80, 5, "%"),
            vol.Required("ev_modus_laden", default=w["ev_modus_laden"]): _select(modi, custom=True),
            vol.Required("ev_modus_wachten", default=w["ev_modus_wachten"]): _select(modi, custom=True),
        }
    )
    return vol.Schema(
        {
            vol.Required(CONF_EV_AAN, default=bool(w[CONF_EV_AAN])): selector.BooleanSelector(),
            vol.Required("apparaten"): section(_entity_schema(hass, EV_ENTITY_KEYS, huidig), {"collapsed": False}),
            vol.Required("regeling"): section(regeling, {"collapsed": False}),
        }
    )


def _wp_schema(hass: HomeAssistant, huidig: dict[str, Any]) -> vol.Schema:
    w = {**STANDAARD_INSTELLINGEN, **{k: v for k, v in huidig.items() if v is not None}}
    regeling = vol.Schema(
        {
            vol.Required("wp_basis_temp", default=w["wp_basis_temp"]): _num(15, 70, 0.5, "°C"),
            vol.Required("wp_verhoging", default=w["wp_verhoging"]): _num(0, 20, 0.5, "K"),
            vol.Required("wp_verlaging", default=w["wp_verlaging"]): _num(0, 20, 0.5, "K"),
            vol.Required("wp_min_temp", default=w["wp_min_temp"]): _num(10, 70, 0.5, "°C"),
            vol.Required("wp_max_temp", default=w["wp_max_temp"]): _num(15, 75, 0.5, "°C"),
            vol.Required("wp_goedkoop_aandeel", default=w["wp_goedkoop_aandeel"]): _num(0, 50, 5, "%"),
            vol.Required("wp_duur_aandeel", default=w["wp_duur_aandeel"]): _num(0, 50, 5, "%"),
            vol.Required("wp_stooklijn", default=w["wp_stooklijn"]): _num(0, 3, 0.1, "K/°C"),
            vol.Required("wp_pv_overschot_w", default=w["wp_pv_overschot_w"]): _num(0, 20000, 100, "W"),
            vol.Required("wp_pv_min_soc", default=w["wp_pv_min_soc"]): _num(0, 100, 1, "%"),
            vol.Required("wp_niet_verlagen_bij_vraag", default=w["wp_niet_verlagen_bij_vraag"]): selector.BooleanSelector(),
            vol.Required(CONF_WP_MIN_MINUTEN, default=w[CONF_WP_MIN_MINUTEN]): _num(0, 240, 5, "min"),
        }
    )
    return vol.Schema(
        {
            vol.Required(CONF_WP_AAN, default=bool(w[CONF_WP_AAN])): selector.BooleanSelector(),
            vol.Required("apparaten"): section(_entity_schema(hass, WP_ENTITY_KEYS, huidig), {"collapsed": False}),
            vol.Required("regeling"): section(regeling, {"collapsed": False}),
        }
    )


class BatterijPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Eerste installatie: bronnen, P1-meter en omvormers kiezen."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        fouten: dict[str, str] = {}
        if user_input is not None:
            fouten = _controleer_bronnen(self.hass, user_input)
            if not fouten:
                self._data.update(_met_lege_optionele(BRON_KEYS, user_input))
                return await self.async_step_meting()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _entity_schema(self.hass, BRON_KEYS, {}), user_input or {}
            ),
            errors=fouten,
        )

    async def async_step_meting(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(_met_lege_optionele(METING_KEYS, user_input))
            return await self.async_step_apparaten()
        return self.async_show_form(step_id="meting", data_schema=_entity_schema(self.hass, METING_KEYS, {}))

    async def async_step_apparaten(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            for nr in range(1, MAX_OMVORMERS + 1):
                self._data[omv_key(CONF_DEVICE, nr)] = user_input.get(omv_key(CONF_DEVICE, nr))
            return await self.async_step_omvormer()
        return self.async_show_form(step_id="apparaten", data_schema=_apparaat_schema({}))

    async def async_step_omvormer(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fouten: dict[str, str] = {}
        if user_input is not None:
            data = _omvormer_data(user_input)
            fouten = _controleer_omvormer(data)
            if not fouten:
                self._data.update(data)
                return self.async_create_entry(
                    title="Batterij Planner Deye", data=self._data, options=dict(STANDAARD_INSTELLINGEN)
                )
        return self.async_show_form(
            step_id="omvormer", data_schema=_omvormer_schema(self.hass, self._data), errors=fouten
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BatterijPlannerOptionsFlow()


class BatterijPlannerOptionsFlow(OptionsFlow):
    """Later aanpassen via Configureren."""

    def __init__(self) -> None:
        self._apparaten: dict[str, Any] = {}

    def _huidig(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _opslaan(self, nieuw: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **nieuw})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["instellingen", "bronnen", "meting", "apparaten", "autolader", "warmtepomp"],
        )

    async def async_step_instellingen(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            plat = _plat(user_input, SECTIES)
            if plat.get("min_soc", 0) >= plat.get("max_soc", 100):
                return self.async_show_form(
                    step_id="instellingen",
                    data_schema=_instellingen_schema(self.hass, {**self._huidig(), **plat}),
                    errors={"base": "soc_bereik"},
                )
            return self._opslaan(plat)
        return self.async_show_form(
            step_id="instellingen", data_schema=_instellingen_schema(self.hass, self._huidig())
        )

    async def async_step_bronnen(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fouten: dict[str, str] = {}
        if user_input is not None:
            fouten = _controleer_bronnen(self.hass, user_input)
            if not fouten:
                return self._opslaan(_met_lege_optionele(BRON_KEYS, user_input))
        return self.async_show_form(
            step_id="bronnen", data_schema=_entity_schema(self.hass, BRON_KEYS, self._huidig()), errors=fouten
        )

    async def async_step_meting(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._opslaan(_met_lege_optionele(METING_KEYS, user_input))
        return self.async_show_form(
            step_id="meting", data_schema=_entity_schema(self.hass, METING_KEYS, self._huidig())
        )

    async def async_step_apparaten(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._apparaten = {
                omv_key(CONF_DEVICE, nr): user_input.get(omv_key(CONF_DEVICE, nr))
                for nr in range(1, MAX_OMVORMERS + 1)
            }
            return await self.async_step_omvormer()
        return self.async_show_form(step_id="apparaten", data_schema=_apparaat_schema(self._huidig()))

    async def async_step_omvormer(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        huidig = {**self._huidig(), **self._apparaten}
        # bij een ander apparaat de oude entiteiten niet meer voorstellen
        for nr in range(1, MAX_OMVORMERS + 1):
            if self._apparaten.get(omv_key(CONF_DEVICE, nr)) != self._huidig().get(omv_key(CONF_DEVICE, nr)):
                for veld in OMVORMER_VELDEN:
                    huidig.pop(omv_key(veld, nr), None)
        fouten: dict[str, str] = {}
        if user_input is not None:
            data = _omvormer_data(user_input)
            fouten = _controleer_omvormer(data)
            if not fouten:
                return self._opslaan({**self._apparaten, **data})
        return self.async_show_form(
            step_id="omvormer", data_schema=_omvormer_schema(self.hass, huidig), errors=fouten
        )

    async def async_step_autolader(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fouten: dict[str, str] = {}
        if user_input is not None:
            apparaten = user_input.get("apparaten", {}) or {}
            regeling = user_input.get("regeling", {}) or {}
            aan = bool(user_input.get(CONF_EV_AAN))
            if aan and not apparaten.get(CONF_EV_MODUS):
                fouten["base"] = "ev_modus_nodig"
            else:
                return self._opslaan({CONF_EV_AAN: aan, **_met_lege_optionele(EV_ENTITY_KEYS, apparaten), **regeling})
        return self.async_show_form(
            step_id="autolader", data_schema=_ev_schema(self.hass, self._huidig()), errors=fouten
        )

    async def async_step_warmtepomp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        fouten: dict[str, str] = {}
        if user_input is not None:
            apparaten = user_input.get("apparaten", {}) or {}
            regeling = user_input.get("regeling", {}) or {}
            aan = bool(user_input.get(CONF_WP_AAN))
            if regeling.get("wp_min_temp", 0) >= regeling.get("wp_max_temp", 100):
                fouten["base"] = "wp_bereik"
            elif aan and not apparaten.get(CONF_WP_SETPOINT):
                fouten["base"] = "wp_setpoint_nodig"
            else:
                return self._opslaan({CONF_WP_AAN: aan, **_met_lege_optionele(WP_ENTITY_KEYS, apparaten), **regeling})
        return self.async_show_form(
            step_id="warmtepomp", data_schema=_wp_schema(self.hass, self._huidig()), errors=fouten
        )
