# Batterij Planner Deye voor Home Assistant

**Versie: 1.0.0** · [Versiegeschiedenis](#versiegeschiedenis) · [Releases](https://github.com/Fripistack/HA-Deye-Battery-Planner/releases)

Batterij Planner Deye is een custom integration die een thuisbatterij achter één of twee **Deye-hybride omvormers** stuurt op de dynamische kwartierprijzen van Frank Energie. Optioneel plant hij ook het laden van de **auto via evcc** en verschuift hij de **warmtepomp** naar goedkope en zonnige uren. Alles stel je in via het scherm, zonder YAML.

Het is de Deye-versie van [Batterij Planner](https://github.com/Fripistack/Fripi-Battery-Planner-V1) (Solis). Beide kunnen naast elkaar geïnstalleerd zijn.

Bij elke berekening worden **alle bekende kwartieren tegelijk** doorgerekend: vandaag en morgen, tot ongeveer 35 uur vooruit. Per kwartier kiest de planner wat het meest oplevert:

| modus | wat de omvormers doen | vermogen |
|---|---|---|
| zelfverbruik | normaal: PV-overschot naar de accu, tekort uit de accu | — |
| vasthouden | accu ontlaadt niet, het huis draait op net en PV (accu sparen voor een duurder moment) | — |
| laden | accu laden uit PV en/of net | 33 %, 66 % of 100 % |
| ontladen | accu ontladen naar huis en net | 33 %, 66 % of 100 % |

De **vermogenstrappen** zijn percentages van het maximale laad- en ontlaadvermogen, geen vaste wattages. Zo kan de planner energie over meer kwartieren spreiden en binnen de aansluitwaarde blijven, ook als de auto tegelijk laadt.

Bij elke beslissing telt mee:

- afname- en terugleverprijs per kwartier, met saldering t/m 2026 en daarna marktprijs + opslag;
- de PV-verwachting (Forecast.Solar of Solcast);
- een zelf geleerd verbruiksprofiel per uur, apart voor werkdag en weekend, berekend uit de P1-meter;
- de geplande autolading;
- laad- en ontlaadverlies, slijtage, min/max SOC, max vermogen en de aansluitwaarde;
- de waarde van de lading die aan het einde van de planning nog in de accu zit.

Elke 5 minuten wordt opnieuw gerekend met de actuele SOC, en direct zodra de prijzen van morgen binnen zijn.

Gemaakt voor: 2× Deye SUN-12K-SG05LP3-EU parallel (via [Solarman / ha-solarman](https://github.com/davidrapan/ha-solarman)), Frank Energie Quarter Prices, Forecast.Solar, een P1-meter (DSMR of HomeWizard) en evcc met een Radian Neo-lader.

## Strategie: nu terugverdienen, vanaf 2027 laagste kosten

De planner zoekt altijd het plan met de **laagste netto energiekosten** over de bekende prijzen. Wat dat in de praktijk betekent, hangt af van hoe teruglevering wordt vergoed:

| periode | teruglevering waard | gedrag | sensor *Strategy* |
|---|---|---|---|
| t/m 31-12-2026 (saldering) | de afnameprijs van dat kwartier | goedkoop laden en in dure kwartieren zoveel mogelijk terugleveren: **maximaal terugverdienen** | Maximise payback |
| vanaf 1-1-2027 | marktprijs + opslag | vooral eigen verbruik uit de accu, alleen terugleveren als het echt loont, export stoppen bij negatieve prijs: **laagste kosten** | Lowest cost |

De overgang gaat per kwartier vanzelf, ook in de nacht van de jaarwissel. Je hoeft er niets voor om te zetten. Saldering geldt alleen tot je jaarafname; lever je over het jaar netto méér terug dan je afneemt, kies dan *Geen saldering*.

---

## Installatie

### 1. Solarman en evcc

- Installeer [ha-solarman](https://github.com/davidrapan/ha-solarman) (HACS) en voeg beide Deye's toe met het profiel **deye_p3.yaml**. Geef ze een herkenbare naam, bijvoorbeeld *deye* (master) en *deye2* (slave).
- Zet op de omvormers (app of display) de zes **Time of Use**-programma's zo dat ze samen de hele dag beslaan, bijvoorbeeld 00:00, 04:00, 08:00, 12:00, 16:00 en 20:00. De planner verandert de tijden niet; hij schrijft in alle zes dezelfde waarden.
- Optioneel: installeer de [evcc-integratie](https://github.com/marq24/ha-evcc) voor de Radian Neo.

### 2. Integratie neerzetten

**Via HACS (aanbevolen):**
1. HACS → ⋮ rechtsboven → **Aangepaste repositories**.
2. URL `https://github.com/Fripistack/HA-Deye-Battery-Planner`, type **Integratie** → Toevoegen.
3. Zoek **Batterij Planner Deye** → **Downloaden**.

**Handmatig:** kopieer de map `custom_components/batterij_planner_deye` naar `config/custom_components/`.

Herstart daarna Home Assistant.

### 3. Integratie toevoegen

Instellingen → **Apparaten & diensten** → **+ Integratie toevoegen** → zoek **Batterij Planner Deye**. Je doorloopt vier schermen:

| scherm | wat je kiest |
|---|---|
| **Data sources** | accu-SOC, Frank-prijzen vandaag en morgen, zonverwachting |
| **P1 meter and power sensors** | P1-afname en -teruglevering (W/kW), P1-meterstanden (kWh, tarief 1 en 2), PV- en accuvermogen van beide Deye's |
| **Inverters** | de Solarman-apparaten van de master en de slave |
| **Inverter entities** | per omvormer: Program 1–6 SOC, Power en Charging, Work Mode, Time of Use, Export Surplus en Battery Grid Charging Current. De planner vult ze zelf in op basis van de gekozen apparaten; controleer ze even. |

### 4. Instellingen controleren

Bij de integratie: **Configureren → Settings**. Controleer vooral:

- **Usable battery capacity**, **Maximum charge/discharge power** (totaal van beide omvormers) en **Grid connection limit** (3 × 25 A ≈ 17.000 W).
- **Use power steps 33 % / 66 % / 100 %** (standaard aan).
- **Inverter control**: de opties van Work Mode, Program Charging en Time of Use worden uit je eigen omvormer gelezen. Normaal *Zero Export To CT*, *Export First*, *Grid*, *Disabled* en *Enabled*.
- **Stop exporting at negative feed-in prices** (standaard aan): zet *Export Surplus* uit in kwartieren waarin terugleveren geld kost.

### 5. Een dag meekijken, daarna aanzetten

Het apparaat **Batterij Planner Deye** heeft een schakelaar **Control inverter**, die standaard uit staat. Zolang die uit staat, rekent de planner wel, maar raakt hij de omvormers niet aan.

1. Kijk een dag mee met de sensor **Mode**. Het attribuut `blokken` toont het hele plan, `deye_doel` wat er in de omvormers gezet zou worden.
2. Klopt het, zet dan **Control inverter** aan.
3. Het attribuut `laatste_schrijfacties` laat zien wat er is geschreven.

Zet je de schakelaar uit, dan gaan alle programma's terug naar gewoon zelfverbruik.

---

## Hoe de omvormers worden aangestuurd

De planner schrijft in **alle zes** TOU-programma's van **beide** omvormers dezelfde waarden, en alleen als een waarde echt verandert. Vermogens worden gelijk over de omvormers verdeeld.

| modus | Work Mode | Program Charging | Program SOC | Program Power | Grid Charging Current |
|---|---|---|---|---|---|
| zelfverbruik | Zero Export To CT | Disabled | min SOC | max ÷ 2 | — |
| vasthouden | Zero Export To CT | Disabled | SOC bij begin van het blok | max ÷ 2 | — |
| laden | Zero Export To CT | Grid | doel-SOC einde blok (afgerond op 5 %) | max ÷ 2 | trap × max ÷ 2 ÷ spanning |
| ontladen | Export First | Disabled | doel-SOC einde blok (afgerond op 5 %) | trap × max ÷ 2 | — |

*Time of Use* wordt op *Enabled* gehouden. Ontbreken gegevens (SOC onbekend, geen prijzen), dan valt de planner terug op zelfverbruik.

Twee dingen moet je één keer zelf testen:

- **Vasthouden:** controleer dat de accu met Program SOC = huidige SOC niet verder ontlaadt.
- **Parallel:** controleer dat de slave dezelfde waarden overneemt. Werkt jouw opstelling met instellingen alleen op de master, laat dan bij *Inverter 2* de programma-entiteiten leeg.

## Autolader (evcc, optioneel)

Configureren → **Car charger (evcc)** → zet *Use the car charger module* aan en kies de evcc-entiteiten van het laadpunt (Mode, Vehicle connected, Vehicle SoC, Charge power, eventueel Limit SoC).

- Benodigde energie = (doel-SOC − huidige SOC) × accucapaciteit ÷ laadrendement.
- De planner kiest de **goedkoopste kwartieren tot het vertrekmoment** (standaard 07:30). In die kwartieren zet hij evcc op **now**, anders op **pv** (alleen zonne-overschot).
- Onder de nood-SOC (standaard 20 %) laadt hij altijd direct.
- De geplande autolading telt mee in het accuplan. De thuisaccu loopt dus niet leeg in de auto als dat niet loont, en samen blijven ze binnen de aansluitwaarde.

De sensor **Car charging** laat zien wat de planner doet en waarom. Pas met de schakelaar **Control car charger** wordt evcc echt omgezet.

## Warmtepomp (optioneel)

Configureren → **Heat pump** → zet *Use the heat pump module* aan. Daarna verschijnen de sensor **Heat pump target temperature** en de schakelaar **Control heat pump**. Zet je de module uit, dan verdwijnen ze weer.

| situatie | streeftemperatuur (standaard) |
|---|---|
| kwartier hoort bij de goedkoopste 25 % van de komende 24 uur | normaal + 3 K → **28 °C** |
| zonne-overschot ≥ 1500 W (P1-export + acculaden) én thuisaccu ≥ 90 % | normaal + 3 K → **28 °C** |
| kwartier hoort bij de duurste 25 % | normaal − 3 K, nooit onder het minimum |
| anders | normaal: **25 °C** (optioneel met stooklijn) |

Je kunt kiezen tussen twee manieren van sturen:

- **Streeftemperatuur** (climate, water_heater of number van Tuya / Tuya Local).
- **SG Ready-contact** (switch of input_boolean voor een relais): aan in goedkope en zonnige kwartieren, anders uit. Dan mag de *weather compensation* van de warmtepomp aan blijven; de pomp verhoogt zelf zijn doel met de *SG cool/heat diff* (bijv. 2 °C).

Altijd binnen min/max en hooguit één wijziging per 30 minuten.

## Entiteiten

| naam | betekenis |
|---|---|
| Mode | huidige modus; attributen: volledig plan (`blokken`, `slots`), kosten, `deye_doel`, fout |
| Power step | vermogenstrap van het huidige blok: 0, 33, 66 of 100 % |
| Strategy | *Maximise payback* (t/m 2026) of *Lowest cost* (vanaf 2027) |
| Expected saving | € beter dan alleen zelfverbruik, over de hele bekende horizon |
| Next action | bijv. `Discharge 66% Tue 06:45–09:00` |
| Target SOC end of block | verwachte SOC aan het eind van het huidige blok |
| Learned consumption last quarter | geleerd verbruik; attributen: profiel per uur |
| Electricity cost today / this month / total | werkelijke kosten uit de P1-meterstanden × Frank-kwartierprijs (negatief = opbrengst) |
| Car charging | *Charging*, *Waiting* of *Not connected* (alleen met autolader-module) |
| Heat pump target temperature | gewenste streeftemperatuur en reden (alleen met warmtepomp-module) |
| Control inverter / Control car charger / Control heat pump | sturing wel/niet aan |
| Recalculate plan | direct opnieuw rekenen |

Elk item in het attribuut `slots` van de Mode-sensor is `[tijd_ms, modus (Z/V/L/O), soc%, afnameprijs, terugleverprijs, pv_kW, verbruik_kW, vermogen_%, auto_kW]`.

## Problemen oplossen

| symptoom | oplossing |
|---|---|
| "Inverter 1 needs at least the Program SOC entities and the Work Mode" | Solarman-entiteiten hebben bij jou een andere naam: kies ze handmatig (Ontwikkelaarshulpmiddelen → Statussen, filter op `program_`) |
| In het logboek "optie 'Enabled' bestaat niet" | Configureren → Settings → Inverter control: kies de optie zoals jouw Solarman-versie hem noemt |
| Mode-sensor heeft attribuut `fout` | daarin staat wat ontbreekt (SOC onbekend / geen prijzen) |

Extra logging aanzetten:

```yaml
logger:
  logs:
    custom_components.batterij_planner_deye: debug
```

## Ontwikkelen

De rekenkern (`optimizer.py`, `deye.py`, `ev.py`, `warmtepomp.py`) heeft geen Home Assistant nodig. Tests: `pip install pytest && pytest tests`. Bij elke push maakt de GitHub Action een release aan voor de versie in `manifest.json`; die moet ook bovenaan deze README staan.

## Versiegeschiedenis

### 1.0.0
- Eerste versie, afgeleid van Batterij Planner 5.2.2 (Solis).
- Sturing van één of twee Deye-hybride omvormers via Solarman: alle zes TOU-programma's (SOC, Power, Charging), Work Mode, Time of Use, Export Surplus en Battery Grid Charging Current. Entiteiten worden automatisch gevonden op het gekozen apparaat.
- Vermogenstrappen 33 % / 66 % / 100 % van het maximum in plaats van vaste waardes.
- Aansluitwaarde: geplande acties blijven binnen het maximale netvermogen.
- P1-meter: huisverbruik wordt berekend uit net + PV + accu (of uit load-sensoren), werkelijke kosten per dag, maand en totaal.
- Strategie: t/m 2026 maximaal terugverdienen (saldering), vanaf 2027 automatisch laagste kosten; export stoppen bij negatieve terugleverprijs.
- Optionele autolader-module via evcc (Radian Neo): laden in de goedkoopste kwartieren tot vertrek, met noodlading en eigen schakelaar.
- Optionele warmtepomp-module met aan/uit in de instellingen en eigen schakelaar; streeftemperatuur of SG Ready-contact.
