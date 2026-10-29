"""Coordinator: plan berekenen, verbruik leren en de Deye-omvormers, evcc en warmtepomp aansturen."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import fields
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import deye, ev, optimizer, warmtepomp
from .const import (
    CONF_ACCU_VERMOGEN,
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
    CONF_WERKMODUS,
    CONF_WP_AAN,
    CONF_WP_BUFFER,
    CONF_WP_BUITEN,
    CONF_WP_MIN_MINUTEN,
    CONF_WP_SETPOINT,
    CONF_WP_THERMOSTAAT,
    DOMAIN,
    GEEN_MELDING,
    MAX_OMVORMERS,
    STANDAARD_INSTELLINGEN,
    omv_key,
)
from .profiel import VerbruiksProfiel

_LOGGER = logging.getLogger(__name__)
ONGELDIG = ("unknown", "unavailable", "none", "")
MODUS_NAAM = {"zelfverbruik": "Self-consumption", "vasthouden": "Hold", "laden": "Charge", "ontladen": "Discharge"}


class PlanFout(Exception):
    """Plan kan niet berekend worden (ontbrekende gegevens)."""


def _float(state: State | None) -> float | None:
    if state is None or state.state in ONGELDIG:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _lijst(waarde: Any) -> list[str]:
    if not waarde:
        return []
    if isinstance(waarde, str):
        return [waarde]
    return [str(v) for v in waarde if v]


class BatterijPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Rekent elke 5 minuten (en bij nieuwe prijzen) het plan opnieuw uit."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.conf: dict[str, Any] = {**STANDAARD_INSTELLINGEN, **entry.data, **entry.options}
        self.actief = False
        self.ev_actief = False
        self.wp_actief = False
        self._wp_laatste_schrijf: datetime | None = None
        self.wp_laatste_actie: str | None = None
        self.ev_laatste_actie: str | None = None
        self.profiel = VerbruiksProfiel(hass, entry.entry_id, float(self.conf["verbruik_fallback_w"]))
        self._kosten_store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.kosten")
        self.kosten: dict[str, Any] = {}
        self._prijs_kwartier: tuple[float, float] | None = None
        self._lock = asyncio.Lock()
        self._unsubs: list = []
        self._melding_gepland = False
        self._laatste_melding: date | None = None
        self._hold_soc: float | None = None
        self._vorige_modus: str | None = None
        self.laatste_schrijfacties: list[str] = []

    # ------------------------------------------------------------------ configuratie
    @property
    def omvormers(self) -> list[dict[str, Any]]:
        """Entiteiten per omvormer (alleen omvormers met ten minste één programma-entiteit)."""
        uit = []
        for nr in range(1, MAX_OMVORMERS + 1):
            o = {
                CONF_PROG_SOC: _lijst(self.conf.get(omv_key(CONF_PROG_SOC, nr))),
                CONF_PROG_POWER: _lijst(self.conf.get(omv_key(CONF_PROG_POWER, nr))),
                CONF_PROG_LADEN: _lijst(self.conf.get(omv_key(CONF_PROG_LADEN, nr))),
                CONF_WERKMODUS: self.conf.get(omv_key(CONF_WERKMODUS, nr)),
                CONF_TOU: self.conf.get(omv_key(CONF_TOU, nr)),
                CONF_SOLAR_SELL: self.conf.get(omv_key(CONF_SOLAR_SELL, nr)),
                CONF_NETLAADSTROOM: self.conf.get(omv_key(CONF_NETLAADSTROOM, nr)),
            }
            if o[CONF_PROG_SOC] or o[CONF_WERKMODUS]:
                uit.append(o)
        return uit

    @property
    def ev_ingeschakeld(self) -> bool:
        return bool(self.conf.get(CONF_EV_AAN)) and bool(self.conf.get(CONF_EV_MODUS))

    @property
    def wp_ingeschakeld(self) -> bool:
        return bool(self.conf.get(CONF_WP_AAN)) and bool(self.conf.get(CONF_WP_SETPOINT))

    def _meetentiteiten(self) -> list[str]:
        ids = [self.conf.get(CONF_P1_VERMOGEN), self.conf.get(CONF_P1_TERUG_VERMOGEN)]
        ids += _lijst(self.conf.get(CONF_LOAD)) + _lijst(self.conf.get(CONF_PV_VERMOGEN))
        ids += _lijst(self.conf.get(CONF_ACCU_VERMOGEN))
        if self.ev_ingeschakeld:
            ids.append(self.conf.get(CONF_EV_VERMOGEN))
        return [i for i in ids if i]

    # ------------------------------------------------------------------ levensloop
    async def async_start(self) -> None:
        await self.profiel.async_load()
        self.kosten = await self._kosten_store.async_load() or {}
        self.profiel.meting(self.huisverbruik_w(), dt_util.now())
        self._unsubs.append(
            async_track_time_change(self.hass, self._tik_plan, minute=list(range(0, 60, 5)), second=20)
        )
        self._unsubs.append(
            async_track_time_change(self.hass, self._tik_kwartier, minute=[0, 15, 30, 45], second=0)
        )
        meet = self._meetentiteiten()
        if meet:
            self._unsubs.append(async_track_state_change_event(self.hass, meet, self._meet_event))
        if self.conf.get(CONF_PRIJS_MORGEN):
            self._unsubs.append(
                async_track_state_change_event(self.hass, [self.conf[CONF_PRIJS_MORGEN]], self._prijzen_event)
            )

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.profiel.async_save()
        await self._kosten_store.async_save(self.kosten)

    # ------------------------------------------------------------------ metingen
    def _watt(self, entity_id: str | None) -> float | None:
        st = self.hass.states.get(entity_id) if entity_id else None
        waarde = _float(st)
        if waarde is None:
            return None
        eenheid = (st.attributes.get("unit_of_measurement") or "W").lower()
        return waarde * 1000.0 if eenheid == "kw" else waarde

    def _som_watt(self, ids: list[str]) -> float | None:
        waarden = [self._watt(i) for i in ids]
        if not ids or any(w is None for w in waarden):
            return None
        return sum(waarden)

    def netvermogen_w(self) -> float | None:
        """P1: afname positief, teruglevering negatief."""
        afname = self._watt(self.conf.get(CONF_P1_VERMOGEN))
        if afname is None:
            return None
        terug_e = self.conf.get(CONF_P1_TERUG_VERMOGEN)
        if terug_e:
            terug = self._watt(terug_e)
            if terug is None:
                return None
            return afname - terug
        return afname

    def huisverbruik_w(self) -> float | None:
        """Verbruik van het huis zonder accu en zonder auto.

        Met load-sensoren: hun som. Anders uit de P1-meter: net + PV + accu-ontlading.
        """
        load = _lijst(self.conf.get(CONF_LOAD))
        if load:
            totaal = self._som_watt(load)
        else:
            net = self.netvermogen_w()
            pv = self._som_watt(_lijst(self.conf.get(CONF_PV_VERMOGEN))) if self.conf.get(CONF_PV_VERMOGEN) else 0.0
            accu = (
                self._som_watt(_lijst(self.conf.get(CONF_ACCU_VERMOGEN))) if self.conf.get(CONF_ACCU_VERMOGEN) else 0.0
            )
            if net is None or pv is None or accu is None:
                return None
            totaal = net + pv + accu
        if totaal is None:
            return None
        if self.ev_ingeschakeld:
            auto = self._watt(self.conf.get(CONF_EV_VERMOGEN))
            if auto:
                totaal -= auto
        return max(0.0, totaal)

    def _kwh_som(self, ids: list[str]) -> float | None:
        totaal = 0.0
        for i in ids:
            st = self.hass.states.get(i)
            w = _float(st)
            if w is None:
                return None
            eenheid = (st.attributes.get("unit_of_measurement") or "kWh").lower()
            totaal += w / 1000.0 if eenheid == "wh" else w
        return totaal

    def _kosten_bijwerken(self, nu: datetime) -> None:
        """Werkelijke kosten uit de P1-meterstanden × de prijs van het afgelopen kwartier."""
        afname = self._kwh_som(_lijst(self.conf.get(CONF_P1_AFNAME_KWH)))
        terug = self._kwh_som(_lijst(self.conf.get(CONF_P1_TERUG_KWH))) if self.conf.get(CONF_P1_TERUG_KWH) else 0.0
        if afname is None or terug is None:
            return
        k = self.kosten
        dag = nu.date().isoformat()
        maand = nu.strftime("%Y-%m")
        if k.get("dag") != dag:
            k.update({"dag": dag, "kosten_vandaag": 0.0, "afname_vandaag": 0.0, "terug_vandaag": 0.0})
        if k.get("maand") != maand:
            k.update({"maand": maand, "kosten_maand": 0.0})
        vorige = k.get("meterstand")
        if vorige and self._prijs_kwartier is not None:
            da = afname - vorige[0]
            dt = terug - vorige[1]
            # sprongen (meterwissel, reset) negeren
            if 0 <= da < 50 and 0 <= dt < 50:
                p_af, p_terug = self._prijs_kwartier
                bedrag = da * p_af - dt * p_terug
                for sleutel in ("kosten_vandaag", "kosten_maand", "kosten_totaal"):
                    k[sleutel] = round(k.get(sleutel, 0.0) + bedrag, 4)
                k["afname_vandaag"] = round(k.get("afname_vandaag", 0.0) + da, 3)
                k["terug_vandaag"] = round(k.get("terug_vandaag", 0.0) + dt, 3)
        k["meterstand"] = [afname, terug]
        self._kosten_store.async_delay_save(lambda: self.kosten, 60)

    # ------------------------------------------------------------------ events
    async def _tik_plan(self, _now: datetime) -> None:
        await self.async_refresh()

    @callback
    def _tik_kwartier(self, now: datetime) -> None:
        self.profiel.afsluiten(now)
        if self.conf.get(CONF_P1_AFNAME_KWH):
            self._kosten_bijwerken(now)
        d = self.data or {}
        if "afnameprijs_nu" in d:
            # prijs van het kwartier dat nu begint; het plan wordt zo meteen herberekend
            slots = d.get("slots") or []
            volgend = next((s for s in slots if s[0] >= int(now.timestamp() * 1000) - 1000), None)
            if volgend:
                self._prijs_kwartier = (volgend[3], volgend[4])

    @callback
    def _meet_event(self, _event: Event[EventStateChangedData]) -> None:
        self.profiel.meting(self.huisverbruik_w(), dt_util.now())

    @callback
    def _prijzen_event(self, event: Event[EventStateChangedData]) -> None:
        nieuw = event.data["new_state"]
        oud = event.data["old_state"]
        had = bool(oud and oud.attributes.get("prices"))
        heeft = bool(nieuw and nieuw.attributes.get("prices"))
        if heeft and not had:
            self._melding_gepland = True
            self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------ update
    async def _async_update_data(self) -> dict[str, Any]:
        async with self._lock:
            nu = dt_util.now()
            try:
                resultaat = await self._bereken(nu)
            except PlanFout as err:
                resultaat = {"modus": "zelfverbruik", "vermogen_pct": 0, "fout": str(err)}
                _LOGGER.warning("Batterij Planner Deye: %s -> zelfverbruik", err)
            except Exception:  # noqa: BLE001 - nooit de omvormer in een rare stand laten
                _LOGGER.exception("Batterij Planner Deye: onverwachte fout bij berekenen")
                resultaat = {"modus": "zelfverbruik", "vermogen_pct": 0,
                             "fout": "Unexpected error, see the Home Assistant log"}
            resultaat["bijgewerkt"] = nu
            resultaat["sturing_actief"] = self.actief
            if self._prijs_kwartier is None and "afnameprijs_nu" in resultaat:
                self._prijs_kwartier = (resultaat["afnameprijs_nu"], resultaat["terugprijs_nu"])
            self._hold_bijwerken(resultaat)
            if self.actief:
                await self._stuur(resultaat)
            if self.ev_ingeschakeld:
                try:
                    await self._stuur_ev(resultaat)
                except Exception:  # noqa: BLE001 - autolader mag de accusturing nooit breken
                    _LOGGER.exception("Batterij Planner Deye: fout in autoladersturing")
            if self.wp_ingeschakeld:
                try:
                    resultaat["warmtepomp"] = await self._warmtepomp(nu, resultaat)
                except Exception:  # noqa: BLE001 - warmtepomp mag de accusturing nooit breken
                    _LOGGER.exception("Batterij Planner Deye: fout in warmtepompsturing")
                    resultaat["warmtepomp"] = {"fout": "Unexpected error, see the Home Assistant log"}
            resultaat["werkelijk"] = {k: v for k, v in self.kosten.items() if k != "meterstand"}
            resultaat["net_w"] = self.netvermogen_w()
            if self._melding_gepland and "fout" not in resultaat:
                self._melding_gepland = False
                await self._meld(resultaat)
            return resultaat

    def _instellingen(self) -> optimizer.Instellingen:
        velden = {f.name: f for f in fields(optimizer.Instellingen)}
        waarden: dict[str, Any] = {}
        for k, v in self.conf.items():
            if k not in velden or v is None:
                continue
            if k == "saldering":
                waarden[k] = v
            elif isinstance(velden[k].default, bool):
                waarden[k] = bool(v)
            else:
                waarden[k] = float(v)
        return optimizer.Instellingen(**waarden)

    def _kwh(self, entity_id: str | None) -> float:
        st = self.hass.states.get(entity_id) if entity_id else None
        waarde = _float(st)
        if waarde is None:
            return 0.0
        eenheid = (st.attributes.get("unit_of_measurement") or "kWh").lower()
        return waarde / 1000.0 if eenheid == "wh" else waarde

    def _attr_lijst(self, entity_id: str | None, naam: str) -> list:
        st = self.hass.states.get(entity_id) if entity_id else None
        waarde = st.attributes.get(naam) if st else None
        return list(waarde) if isinstance(waarde, (list, tuple)) else []

    def _ev_instellingen(self) -> ev.EvInstellingen:
        namen = {f.name for f in fields(ev.EvInstellingen)}
        waarden: dict[str, Any] = {}
        for k, v in self.conf.items():
            if k.startswith("ev_") and k[3:] in namen and v is not None:
                waarden[k[3:]] = str(v) if k == "ev_vertrek" else float(v)
        return ev.EvInstellingen(**waarden)

    def _ev_plan(self, nu: datetime, slots: list[optimizer.Slot]) -> ev.EvPlan:
        c = self.conf
        verbonden_st = self.hass.states.get(c.get(CONF_EV_VERBONDEN) or "")
        verbonden = None if verbonden_st is None else verbonden_st.state == "on"
        if verbonden_st is None:
            # zonder verbonden-sensor: aannemen dat de auto er is als de SOC bekend is
            verbonden = _float(self.hass.states.get(c.get(CONF_EV_SOC) or "")) is not None
        doel_soc = _float(self.hass.states.get(c.get(CONF_EV_DOEL_SOC_ENTITY) or ""))
        return ev.plan(
            self._ev_instellingen(),
            nu,
            [s.start for s in slots],
            [s.frac for s in slots],
            [s.afname for s in slots],
            [s.pv - s.verbruik for s in slots],
            verbonden,
            _float(self.hass.states.get(c.get(CONF_EV_SOC) or "")),
            doel_soc,
        )

    async def _bereken(self, nu: datetime) -> dict[str, Any]:
        inst = self._instellingen()
        soc = _float(self.hass.states.get(self.conf.get(CONF_SOC) or ""))
        if soc is None:
            raise PlanFout("Battery SOC unavailable")
        tz = dt_util.get_default_time_zone()
        prijzen = [
            self._attr_lijst(self.conf.get(CONF_PRIJS_VANDAAG), "prices"),
            self._attr_lijst(self.conf.get(CONF_PRIJS_MORGEN), "prices"),
        ]
        slots = optimizer.bouw_slots(prijzen, nu, tz)
        if not slots:
            raise PlanFout("No prices available")

        optimizer.zet_terugleverprijs(slots, inst)
        optimizer.zet_verbruik(slots, self.profiel.werkdag, self.profiel.weekend, inst.verbruik_fallback_w)

        solcast = self._attr_lijst(self.conf.get(CONF_SOLCAST_VANDAAG), "detailedForecast") + self._attr_lijst(
            self.conf.get(CONF_SOLCAST_MORGEN), "detailedForecast"
        )
        if solcast and optimizer.zet_pv_solcast(slots, solcast, tz, inst.pv_factor):
            pv_bron = "solcast"
        else:
            pv_bron = "forecast_solar"
            vandaag = nu.date()
            totalen = {
                vandaag: self._kwh(self.conf.get(CONF_PV_REST)),
                vandaag + timedelta(days=1): self._kwh(self.conf.get(CONF_PV_MORGEN)),
            }
            zon: dict[date, tuple[datetime, datetime]] = {}
            for d in {s.start.date() for s in slots}:
                op = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, d)
                onder = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, d)
                if op and onder:
                    zon[d] = (dt_util.as_local(op), dt_util.as_local(onder))
            optimizer.zet_pv_verdeeld(slots, totalen, zon, inst.pv_factor)

        ev_plan = None
        if self.ev_ingeschakeld:
            ev_plan = self._ev_plan(nu, slots)
            optimizer.zet_ev(slots, ev_plan.per_slot_kwh)

        resultaat = await self.hass.async_add_executor_job(optimizer.optimaliseer, slots, soc, inst)
        resultaat["pv_bron"] = pv_bron
        resultaat["huisverbruik_w"] = self.huisverbruik_w()
        if ev_plan is not None:
            resultaat["auto"] = {
                "stand": ev_plan.stand,
                "reden": ev_plan.reden,
                "nodig_kwh": ev_plan.nodig_kwh,
                "vertrek": ev_plan.vertrek.isoformat() if ev_plan.vertrek else None,
                "kwartieren": [slots[i].start.isoformat() for i in ev_plan.kwartieren],
            }
        return resultaat

    # ------------------------------------------------------------------ sturing Deye
    def _hold_bijwerken(self, res: dict[str, Any]) -> None:
        """Bij het begin van een vasthoud-blok de SOC onthouden, zodat het doel niet meeloopt."""
        modus = res.get("modus")
        if modus == "vasthouden" and self._vorige_modus != "vasthouden":
            self._hold_soc = res.get("soc_nu")
        elif modus != "vasthouden":
            self._hold_soc = None
        self._vorige_modus = modus

    def _deye_opties(self) -> deye.DeyeOpties:
        c = self.conf
        return deye.DeyeOpties(
            min_soc=float(c["min_soc"]),
            max_soc=float(c["max_soc"]),
            max_laad_w=float(c["max_laad_w"]),
            max_ontlaad_w=float(c["max_ontlaad_w"]),
            spanning=float(c.get(CONF_SPANNING) or 51.2),
            modus_zelf=str(c[CONF_MODUS_ZELF]),
            modus_ontladen=str(c[CONF_MODUS_ONTLADEN]),
            laden_aan=str(c[CONF_LADEN_AAN]),
            laden_uit=str(c[CONF_LADEN_UIT]),
            tou_aan=str(c[CONF_TOU_AAN]),
            export_blokkeren=bool(c.get(CONF_EXPORT_BLOKKEREN)),
        )

    async def async_naar_zelfverbruik(self) -> None:
        """Bij uitzetten van de sturing: alle programma's terug naar gewoon zelfverbruik."""
        async with self._lock:
            await self._stuur({"modus": "zelfverbruik", "vermogen_pct": 0, "soc_nu": None})

    def _schrijflijst(self, res: dict[str, Any]) -> list[tuple[str, str, Any]]:
        o = self._deye_opties()
        modus = "zelfverbruik" if "fout" in res else res.get("modus", "zelfverbruik")
        fractie = float(res.get("vermogen_pct") or 100) / 100.0
        omvormers = self.omvormers
        d = deye.doel(
            modus,
            fractie,
            float(res.get("soc_nu") or o.min_soc),
            res.get("soc_doel_einde_blok"),
            self._hold_soc,
            res.get("terugprijs_nu"),
            len(omvormers),
            o,
        )
        res["deye_doel"] = {
            "werkmodus": d.werkmodus, "netladen": d.laden, "programma_soc": d.soc,
            "programma_vermogen_w": d.vermogen_w, "netlaadstroom_a": d.netlaadstroom_a,
            "solar_sell": d.solar_sell,
        }
        lijst: list[tuple[str, str, Any]] = []
        for omv in omvormers:
            if omv[CONF_TOU]:
                lijst.append(("aan", omv[CONF_TOU], o.tou_aan))
            if omv[CONF_WERKMODUS]:
                lijst.append(("select", omv[CONF_WERKMODUS], d.werkmodus))
            if omv[CONF_SOLAR_SELL]:
                lijst.append(("switch", omv[CONF_SOLAR_SELL], d.solar_sell))
            if d.netlaadstroom_a is not None and omv[CONF_NETLAADSTROOM]:
                lijst.append(("number", omv[CONF_NETLAADSTROOM], d.netlaadstroom_a))
            for eid in omv[CONF_PROG_SOC]:
                lijst.append(("number", eid, d.soc))
            for eid in omv[CONF_PROG_POWER]:
                lijst.append(("number", eid, d.vermogen_w))
            for eid in omv[CONF_PROG_LADEN]:
                lijst.append(("aan", eid, o.laden_aan if d.laden else o.laden_uit))
        return lijst

    async def _schrijf(self, domein: str, entity_id: str, waarde: Any) -> bool:
        """Schrijf één waarde als die afwijkt. True als er geschreven is."""
        st = self.hass.states.get(entity_id)
        if st is None or st.state == "unavailable":
            _LOGGER.warning("Batterij Planner Deye: %s niet beschikbaar, overgeslagen", entity_id)
            return False
        ent_domein = entity_id.split(".", 1)[0]
        if domein == "aan":
            # select met een optie, of een schakelaar (aan als de optie niet 'uit' betekent)
            if ent_domein == "select":
                domein = "select"
            else:
                domein, waarde = "switch", str(waarde).lower() not in ("disabled", "off", "uit", "false")
        if domein == "number":
            huidig = _float(st)
            if huidig is not None and abs(huidig - float(waarde)) < 0.5:
                return False
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": entity_id, "value": waarde}, blocking=True
            )
        elif domein == "switch":
            if (st.state == "on") == bool(waarde):
                return False
            await self.hass.services.async_call(
                ent_domein if ent_domein in ("switch", "input_boolean") else "homeassistant",
                "turn_on" if waarde else "turn_off", {"entity_id": entity_id}, blocking=True,
            )
        else:
            optie = deye.kies_optie(str(waarde), st.attributes.get("options"))
            if optie is None:
                _LOGGER.warning("Batterij Planner Deye: optie '%s' bestaat niet voor %s (%s)",
                                waarde, entity_id, st.attributes.get("options"))
                return False
            if st.state == optie:
                return False
            await self.hass.services.async_call(
                "select", "select_option", {"entity_id": entity_id, "option": optie}, blocking=True
            )
        return True

    async def _stuur(self, res: dict[str, Any]) -> None:
        acties: list[str] = []
        for domein, entity_id, waarde in self._schrijflijst(res):
            try:
                if await self._schrijf(domein, entity_id, waarde):
                    acties.append(f"{entity_id} = {waarde}")
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.error("Batterij Planner Deye: schrijven naar %s mislukt: %s", entity_id, err)
        if acties:
            _LOGGER.info("Batterij Planner Deye (%s %s%%): %s", res.get("modus"), res.get("vermogen_pct"),
                         ", ".join(acties))
            self.laatste_schrijfacties = acties

    # ------------------------------------------------------------------ autolader (evcc)
    async def _stuur_ev(self, res: dict[str, Any]) -> None:
        auto = res.get("auto")
        if not auto or not self.ev_actief or auto["stand"] == ev.NIET_VERBONDEN:
            return
        optie = self.conf["ev_modus_laden"] if auto["stand"] == ev.LADEN else self.conf["ev_modus_wachten"]
        entity_id = self.conf[CONF_EV_MODUS]
        try:
            if await self._schrijf("select", entity_id, optie):
                self.ev_laatste_actie = f"{dt_util.now():%H:%M} {optie} – {auto['reden']}"
                _LOGGER.info("Batterij Planner Deye autolader: %s", self.ev_laatste_actie)
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.error("Batterij Planner Deye: evcc-modus schrijven mislukt: %s", err)
        auto["laatste_actie"] = self.ev_laatste_actie

    async def async_ev_normaal(self) -> None:
        """Bij uitzetten van de autoladersturing: evcc terug naar de wachtmodus (pv)."""
        entity_id = self.conf.get(CONF_EV_MODUS)
        if not entity_id:
            return
        try:
            await self._schrijf("select", entity_id, self.conf["ev_modus_wachten"])
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.error("Batterij Planner Deye: evcc-modus schrijven mislukt: %s", err)

    # ------------------------------------------------------------------ melding
    async def _meld(self, res: dict[str, Any]) -> None:
        dienst = self.conf.get(CONF_NOTIFY)
        vandaag = dt_util.now().date()
        if not dienst or dienst == GEEN_MELDING or self._laatste_melding == vandaag:
            return
        morgen = vandaag + timedelta(days=1)
        regels = []
        for b in res.get("blokken", []):
            eind = dt_util.parse_datetime(b["eind"])
            if eind and dt_util.as_local(eind).date() >= morgen and b["modus"] != "zelfverbruik":
                start = dt_util.as_local(dt_util.parse_datetime(b["start"]))
                e = dt_util.as_local(eind)
                naam = MODUS_NAAM[b["modus"]]
                if b.get("vermogen_pct"):
                    naam += f" {b['vermogen_pct']}%"
                regels.append(
                    f"{start:%H:%M}–{e:%H:%M} {naam}: SOC {b['soc_start']:.0f}% → {b['soc_eind']:.0f}%, "
                    f"avg. price € {b['afname_gem']:.3f}/kWh"
                )
        domein, _, naam = dienst.partition(".")
        try:
            await self.hass.services.async_call(
                domein or "notify",
                naam,
                {
                    "title": f"🔋 Deye plan for tomorrow – expected € {res.get('besparing', 0):.2f} better than self-consumption only",
                    "message": "\n".join(regels) or "Tomorrow: self-consumption only, no charging or discharging planned.",
                },
                blocking=False,
            )
            self._laatste_melding = vandaag
        except HomeAssistantError as err:
            _LOGGER.error("Batterij Planner Deye: melding versturen mislukt: %s", err)

    # ------------------------------------------------------------------ warmtepomp
    def _wp_instellingen(self) -> warmtepomp.WpInstellingen:
        namen = {f.name for f in fields(warmtepomp.WpInstellingen)}
        waarden: dict[str, Any] = {}
        for k, v in self.conf.items():
            if k.startswith("wp_") and k[3:] in namen and v is not None:
                waarden[k[3:]] = bool(v) if k == "wp_niet_verlagen_bij_vraag" else float(v)
        return warmtepomp.WpInstellingen(**waarden)

    @staticmethod
    def _wp_is_contact(entity_id: str) -> bool:
        """SG Ready-contact (relais) in plaats van een streeftemperatuur."""
        return entity_id.startswith(("switch.", "input_boolean."))

    def _wp_huidig_setpoint(self, entity_id: str) -> float | None:
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        if self._wp_is_contact(entity_id):
            return 1.0 if st.state == "on" else 0.0
        if entity_id.startswith(("climate.", "water_heater.")):
            try:
                return float(st.attributes.get("temperature"))
            except (TypeError, ValueError):
                return None
        return _float(st)

    async def _wp_schrijf(self, entity_id: str, temp: float) -> None:
        domein = entity_id.split(".", 1)[0]
        if self._wp_is_contact(entity_id):
            await self.hass.services.async_call(
                domein, "turn_on" if temp >= 1 else "turn_off", {"entity_id": entity_id}, blocking=True
            )
        elif domein in ("climate", "water_heater"):
            await self.hass.services.async_call(
                domein, "set_temperature", {"entity_id": entity_id, "temperature": temp}, blocking=True
            )
        else:
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": entity_id, "value": temp}, blocking=True
            )

    async def _warmtepomp(self, nu: datetime, res: dict[str, Any]) -> dict[str, Any] | None:
        setpoint_e = self.conf.get(CONF_WP_SETPOINT)
        if not setpoint_e:
            return None
        inst = self._wp_instellingen()
        slots = res.get("slots") or []
        prijzen = [s[3] for s in slots[:96]]
        huidige_prijs = prijzen[0] if prijzen else None

        buiten = _float(self.hass.states.get(self.conf.get(CONF_WP_BUITEN) or ""))
        net = self.netvermogen_w()
        # zonne-overschot = wat er nu naar het net gaat (P1), plus wat de accu nu laadt
        accu_ids = _lijst(self.conf.get(CONF_ACCU_VERMOGEN))
        accu = self._som_watt(accu_ids) if accu_ids else 0.0
        overschot = None
        if net is not None and accu is not None:
            overschot = max(0.0, -net) + max(0.0, -accu)
        soc = _float(self.hass.states.get(self.conf.get(CONF_SOC) or ""))
        thermostaat = self.hass.states.get(self.conf.get(CONF_WP_THERMOSTAAT) or "")
        vraag = None
        if thermostaat is not None:
            vraag = thermostaat.attributes.get("hvac_action") == "heating"

        besluit = warmtepomp.bepaal(inst, huidige_prijs, prijzen, buiten, overschot, soc, vraag)
        contact = self._wp_is_contact(setpoint_e)
        gewenst = (1.0 if besluit.stand == warmtepomp.BOOST else 0.0) if contact else besluit.doel
        huidig = self._wp_huidig_setpoint(setpoint_e)
        uit = {
            "doel": besluit.doel,
            "stand": besluit.stand,
            "reden": besluit.reden,
            "basis": round(besluit.basis, 1),
            "prijs_rang": None if besluit.prijs_rang is None else round(besluit.prijs_rang, 2),
            "sg_ready_contact": contact,
            "huidig_setpoint": huidig,
            "buffer_temp": _float(self.hass.states.get(self.conf.get(CONF_WP_BUFFER) or "")),
            "buiten_temp": buiten,
            "zon_overschot_w": None if overschot is None else round(overschot),
            "warmtevraag": vraag,
            "sturing_actief": self.wp_actief,
            "laatste_actie": self.wp_laatste_actie,
        }
        if not self.wp_actief or huidig is None:
            return uit
        if abs(huidig - gewenst) < 0.5:
            return uit
        min_min = float(self.conf.get(CONF_WP_MIN_MINUTEN) or 0)
        if self._wp_laatste_schrijf and (nu - self._wp_laatste_schrijf) < timedelta(minutes=min_min):
            uit["wacht_tot"] = (self._wp_laatste_schrijf + timedelta(minutes=min_min)).isoformat()
            return uit
        try:
            await self._wp_schrijf(setpoint_e, gewenst)
            self._wp_laatste_schrijf = nu
            if contact:
                self.wp_laatste_actie = f"{nu:%H:%M} SG Ready {'on' if gewenst else 'off'} ({besluit.stand})"
            else:
                self.wp_laatste_actie = f"{nu:%H:%M} {huidig:g} → {gewenst:g} °C ({besluit.stand})"
            uit["laatste_actie"] = self.wp_laatste_actie
            _LOGGER.info("Batterij Planner Deye warmtepomp: %s – %s", self.wp_laatste_actie, besluit.reden)
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.error("Batterij Planner Deye: warmtepomp schrijven mislukt: %s", err)
        return uit

    async def async_wp_normaal(self) -> None:
        """Bij uitzetten van de warmtepompsturing: terug naar normaal (of SG Ready-contact uit)."""
        setpoint_e = self.conf.get(CONF_WP_SETPOINT)
        if not setpoint_e:
            return
        if self._wp_is_contact(setpoint_e):
            basis = 0.0
        else:
            inst = self._wp_instellingen()
            buiten = _float(self.hass.states.get(self.conf.get(CONF_WP_BUITEN) or ""))
            basis = warmtepomp.bepaal(inst, None, [], buiten).doel
        huidig = self._wp_huidig_setpoint(setpoint_e)
        if huidig is None or abs(huidig - basis) < 0.5:
            return
        try:
            await self._wp_schrijf(setpoint_e, basis)
            self._wp_laatste_schrijf = dt_util.now()
            self.wp_laatste_actie = f"{dt_util.now():%H:%M} back to normal (control switched off)"
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.error("Batterij Planner Deye: warmtepomp schrijven mislukt: %s", err)
