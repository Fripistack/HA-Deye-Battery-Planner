"""Constanten voor Batterij Planner Deye."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "batterij_planner_deye"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]
MAX_OMVORMERS = 2

# ---- bronnen (stap 1)
CONF_SOC = "soc_entity"
CONF_PRIJS_VANDAAG = "prijs_vandaag_entity"
CONF_PRIJS_MORGEN = "prijs_morgen_entity"
CONF_PV_REST = "pv_rest_vandaag_entity"
CONF_PV_MORGEN = "pv_morgen_entity"
CONF_SOLCAST_VANDAAG = "solcast_vandaag_entity"
CONF_SOLCAST_MORGEN = "solcast_morgen_entity"

# ---- metingen (stap 2): P1-meter en vermogens van de omvormers
CONF_P1_VERMOGEN = "p1_vermogen_entity"
CONF_P1_TERUG_VERMOGEN = "p1_teruglever_vermogen_entity"
CONF_P1_AFNAME_KWH = "p1_afname_kwh_entities"
CONF_P1_TERUG_KWH = "p1_teruglever_kwh_entities"
CONF_LOAD = "load_entities"
CONF_PV_VERMOGEN = "pv_vermogen_entities"
CONF_ACCU_VERMOGEN = "accu_vermogen_entities"

# ---- omvormers (stap 3). Per omvormer dezelfde sleutels met achtervoegsel _1 / _2.
CONF_DEVICE = "device"
CONF_PROG_SOC = "programma_soc"
CONF_PROG_POWER = "programma_vermogen"
CONF_PROG_LADEN = "programma_laden"
CONF_WERKMODUS = "werkmodus"
CONF_TOU = "time_of_use"
CONF_SOLAR_SELL = "solar_sell"
CONF_NETLAADSTROOM = "netlaadstroom"
OMVORMER_VELDEN = [CONF_PROG_SOC, CONF_PROG_POWER, CONF_PROG_LADEN, CONF_WERKMODUS,
                   CONF_TOU, CONF_SOLAR_SELL, CONF_NETLAADSTROOM]
OMVORMER_MEERDERE = {CONF_PROG_SOC, CONF_PROG_POWER, CONF_PROG_LADEN}


def omv_key(veld: str, nr: int) -> str:
    return f"omv{nr}_{veld}"


# ---- instellingen omvormer (options)
CONF_SPANNING = "accu_spanning"
CONF_MODUS_ZELF = "werkmodus_zelfverbruik"
CONF_MODUS_ONTLADEN = "werkmodus_ontladen"
CONF_LADEN_AAN = "programma_laden_aan"
CONF_LADEN_UIT = "programma_laden_uit"
CONF_TOU_AAN = "time_of_use_aan"
CONF_EXPORT_BLOKKEREN = "export_blokkeren_negatief"
CONF_NOTIFY = "notify_service"
CONF_TRAPPEN = "vermogenstrappen"

# ---- autolader via evcc (options, optioneel)
CONF_EV_AAN = "ev_ingeschakeld"
CONF_EV_MODUS = "ev_modus_entity"
CONF_EV_VERBONDEN = "ev_verbonden_entity"
CONF_EV_SOC = "ev_soc_entity"
CONF_EV_VERMOGEN = "ev_vermogen_entity"
CONF_EV_DOEL_SOC_ENTITY = "ev_doel_soc_entity"
EV_ENTITY_KEYS = [CONF_EV_MODUS, CONF_EV_VERBONDEN, CONF_EV_SOC, CONF_EV_VERMOGEN, CONF_EV_DOEL_SOC_ENTITY]
EV_INSTELLING_KEYS = [
    "ev_capaciteit_kwh", "ev_laadvermogen_w", "ev_rendement", "ev_doel_soc", "ev_vertrek",
    "ev_modus_laden", "ev_modus_wachten", "ev_noodlaad_soc",
]

# ---- warmtepomp (options, optioneel)
CONF_WP_AAN = "wp_ingeschakeld"
CONF_WP_SETPOINT = "wp_setpoint_entity"
CONF_WP_BUFFER = "wp_buffer_temp_entity"
CONF_WP_BUITEN = "wp_buiten_temp_entity"
CONF_WP_THERMOSTAAT = "wp_thermostaat_entity"
CONF_WP_MIN_MINUTEN = "wp_min_minuten"
WP_ENTITY_KEYS = [CONF_WP_SETPOINT, CONF_WP_BUFFER, CONF_WP_BUITEN, CONF_WP_THERMOSTAAT]
WP_INSTELLING_KEYS = [
    "wp_basis_temp", "wp_verhoging", "wp_verlaging", "wp_min_temp", "wp_max_temp",
    "wp_goedkoop_aandeel", "wp_duur_aandeel", "wp_stooklijn", "wp_pv_overschot_w",
    "wp_pv_min_soc", "wp_niet_verlagen_bij_vraag", CONF_WP_MIN_MINUTEN,
]

GEEN_MELDING = "__geen__"

BRON_KEYS = [CONF_SOC, CONF_PRIJS_VANDAAG, CONF_PRIJS_MORGEN, CONF_PV_REST, CONF_PV_MORGEN,
             CONF_SOLCAST_VANDAAG, CONF_SOLCAST_MORGEN]
METING_KEYS = [CONF_P1_VERMOGEN, CONF_P1_TERUG_VERMOGEN, CONF_P1_AFNAME_KWH, CONF_P1_TERUG_KWH,
               CONF_LOAD, CONF_PV_VERMOGEN, CONF_ACCU_VERMOGEN]
METING_MEERDERE = {CONF_P1_AFNAME_KWH, CONF_P1_TERUG_KWH, CONF_LOAD, CONF_PV_VERMOGEN, CONF_ACCU_VERMOGEN}

# Voorgestelde entiteiten (Frank Quarter Prices, Forecast.Solar, DSMR/P1 en Solarman met
# apparaatnaam "deye"). Worden alleen voorgesteld als ze bestaan.
SUGGESTIES = {
    CONF_SOC: "sensor.deye_battery",
    CONF_PRIJS_VANDAAG: "sensor.frank_prices_today",
    CONF_PRIJS_MORGEN: "sensor.frank_prices_tomorrow",
    CONF_PV_REST: "sensor.energy_production_today_remaining",
    CONF_PV_MORGEN: "sensor.energy_production_tomorrow",
    CONF_P1_VERMOGEN: "sensor.electricity_meter_power_consumption",
    CONF_P1_TERUG_VERMOGEN: "sensor.electricity_meter_power_production",
    CONF_EV_MODUS: "select.evcc_garage_mode",
}

# Standaardwaarden voor de rekeninstellingen (namen = velden van optimizer.Instellingen)
STANDAARD_INSTELLINGEN: dict[str, float | str | bool] = {
    "capaciteit_kwh": 20.0,
    "min_soc": 10.0,
    "max_soc": 100.0,
    "max_laad_w": 10000.0,
    "max_ontlaad_w": 10000.0,
    "rendement_laden": 95.0,
    "rendement_ontladen": 95.0,
    "slijtage_eur_kwh": 0.03,
    "max_net_w": 17000.0,
    "pv_factor": 0.9,
    "saldering": "auto",
    "saldering_correctie": 0.0,
    "teruglever_opslag": 0.0182,
    "eindwaarde_factor": 1.0,
    "verbruik_fallback_w": 500.0,
    CONF_TRAPPEN: True,
    CONF_SPANNING: 51.2,
    CONF_MODUS_ZELF: "Zero Export To CT",
    CONF_MODUS_ONTLADEN: "Export First",
    CONF_LADEN_AAN: "Grid",
    CONF_LADEN_UIT: "Disabled",
    CONF_TOU_AAN: "Enabled",
    CONF_EXPORT_BLOKKEREN: True,
    CONF_NOTIFY: GEEN_MELDING,
    CONF_EV_AAN: False,
    "ev_capaciteit_kwh": 60.0,
    "ev_laadvermogen_w": 11000.0,
    "ev_rendement": 90.0,
    "ev_doel_soc": 80.0,
    "ev_vertrek": "07:30:00",
    "ev_modus_laden": "now",
    "ev_modus_wachten": "pv",
    "ev_noodlaad_soc": 20.0,
    CONF_WP_AAN: False,
    "wp_basis_temp": 25.0,
    "wp_verhoging": 3.0,
    "wp_verlaging": 3.0,
    "wp_min_temp": 25.0,
    "wp_max_temp": 35.0,
    "wp_goedkoop_aandeel": 25.0,
    "wp_duur_aandeel": 25.0,
    "wp_stooklijn": 0.0,
    "wp_pv_overschot_w": 1500.0,
    "wp_pv_min_soc": 90.0,
    "wp_niet_verlagen_bij_vraag": True,
    CONF_WP_MIN_MINUTEN: 30.0,
}
