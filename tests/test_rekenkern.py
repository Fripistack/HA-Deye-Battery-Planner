from datetime import datetime, timedelta, timezone

from conftest import laad

optimizer = laad("optimizer")
ev = laad("ev")
deye = laad("deye")
warmtepomp = laad("warmtepomp")

TZ = timezone(timedelta(hours=1))


def _slots(prijzen, start=datetime(2026, 11, 2, 0, 0, tzinfo=TZ), verbruik_w=500):
    blokken = []
    for i, p in enumerate(prijzen):
        s = start + timedelta(minutes=15 * i)
        blokken.append({"from": s.isoformat(), "till": (s + timedelta(minutes=15)).isoformat(),
                        "total_price_eur_kwh": p, "market_price": p - 0.15})
    slots = optimizer.bouw_slots([blokken], start, TZ)
    inst = optimizer.Instellingen()
    optimizer.zet_terugleverprijs(slots, inst)
    optimizer.zet_verbruik(slots, None, None, verbruik_w)
    return slots, inst


def test_goedkoop_laden_duur_ontladen():
    prijzen = [0.10] * 16 + [0.40] * 16 + [0.25] * 64
    slots, inst = _slots(prijzen)
    res = optimizer.optimaliseer(slots, 20.0, inst)
    assert any(s[1] == "L" for s in res["slots"][:16])
    assert res["strategie"] == "terugverdienen"
    modi = [b["modus"] for b in res["blokken"]]
    assert "ontladen" in modi
    assert res["besparing"] > 0


def test_vermogenstrappen_worden_gebruikt():
    # lange goedkope periode: geen reden om op vol vermogen te laden als 33 % volstaat
    prijzen = [0.10 + 0.0001 * i for i in range(32)] + [0.40] * 8 + [0.25] * 56
    slots, inst = _slots(prijzen)
    inst.capaciteit_kwh = 10.0
    res = optimizer.optimaliseer(slots, 10.0, inst)
    pcts = {s[7] for s in res["slots"] if s[1] in ("L", "O")}
    assert pcts - {100}, pcts


def test_zonder_trappen_alleen_100():
    prijzen = [0.10] * 32 + [0.40] * 8 + [0.25] * 56
    slots, inst = _slots(prijzen)
    inst.vermogenstrappen = False
    res = optimizer.optimaliseer(slots, 10.0, inst)
    assert {s[7] for s in res["slots"] if s[1] in ("L", "O")} <= {100}


def test_aansluitwaarde_beperkt_laden():
    prijzen = [0.05] * 8 + [0.40] * 8 + [0.25] * 80
    slots, inst = _slots(prijzen)
    inst.max_net_w = 5000.0  # kleiner dan 10 kW laadvermogen
    res = optimizer.optimaliseer(slots, 10.0, inst)
    for s in res["slots"][:8]:
        if s[1] == "L":
            assert s[7] < 100


def test_strategie_2027_laagste_kosten():
    prijzen = [0.25] * 96
    slots, inst = _slots(prijzen, start=datetime(2027, 1, 5, 0, 0, tzinfo=TZ))
    res = optimizer.optimaliseer(slots, 50.0, inst)
    assert res["strategie"] == "laagste_kosten"
    assert slots[0].terug < slots[0].afname


def test_ev_kiest_goedkoopste_kwartieren():
    nu = datetime(2026, 11, 2, 18, 0, tzinfo=TZ)
    starts = [nu + timedelta(minutes=15 * i) for i in range(60)]
    prijzen = [0.30] * 24 + [0.10] * 8 + [0.30] * 28
    inst = ev.EvInstellingen(capaciteit_kwh=60, laadvermogen_w=11000, rendement=100, doel_soc=80, vertrek="07:30")
    p = ev.plan(inst, nu, starts, [1.0] * 60, prijzen, None, True, 70.0)
    # 6 kWh nodig = 3 kwartieren van 2,75 kWh
    assert p.stand == ev.WACHTEN
    assert p.kwartieren == [24, 25, 26]
    assert abs(sum(p.per_slot_kwh) - 6.0) < 1e-6


def test_ev_nood_en_niet_verbonden():
    nu = datetime(2026, 11, 2, 18, 0, tzinfo=TZ)
    starts = [nu + timedelta(minutes=15 * i) for i in range(8)]
    inst = ev.EvInstellingen()
    assert ev.plan(inst, nu, starts, [1.0] * 8, [0.3] * 8, None, False, 50).stand == ev.NIET_VERBONDEN
    assert ev.plan(inst, nu, starts, [1.0] * 8, [0.3] * 8, None, True, 10).stand == ev.LADEN


def test_deye_ontdekken():
    ids = [f"number.deye_program_{i}_soc" for i in (3, 1, 2, 4, 5, 6)] + [
        f"number.deye_program_{i}_power" for i in range(1, 7)] + [
        f"select.deye_program_{i}_charging" for i in range(1, 7)] + [
        "select.deye_work_mode", "select.deye_time_of_use", "switch.deye_export_surplus",
        "number.deye_battery_grid_charging_current", "time.deye_program_1_time", "sensor.deye_battery_power"]
    g = deye.ontdek(ids)
    assert g["programma_soc"][0] == "number.deye_program_1_soc"
    assert len(g["programma_soc"]) == 6 and len(g["programma_laden"]) == 6
    assert g["werkmodus"] == "select.deye_work_mode"
    assert g["solar_sell"] == "switch.deye_export_surplus"
    assert g["netlaadstroom"] == "number.deye_battery_grid_charging_current"


def test_deye_doel():
    o = deye.DeyeOpties(min_soc=10, max_soc=100, max_laad_w=10000, max_ontlaad_w=10000)
    d = deye.doel("laden", 2 / 3, 40, 71.2, None, 0.2, 2, o)
    assert d.laden and d.soc == 75 and d.netlaadstroom_a == int(6666.67 / 2 / 51.2)
    d = deye.doel("ontladen", 1 / 3, 80, 52, None, 0.3, 2, o)
    assert d.werkmodus == "Export First" and d.soc == 50 and d.vermogen_w == 1666
    d = deye.doel("vasthouden", 0, 63.4, None, 61.0, 0.2, 2, o)
    assert not d.laden and d.soc == 61
    d = deye.doel("zelfverbruik", 1, 63, None, None, -0.05, 2, o)
    assert d.soc == 10 and d.solar_sell is False
    assert deye.kies_optie("zero export to ct", ["Export First", "Zero Export To CT"]) == "Zero Export To CT"
    assert deye.kies_optie("Grid", ["Disabled"]) is None


def test_warmtepomp_goedkoop_boost():
    inst = warmtepomp.WpInstellingen()
    b = warmtepomp.bepaal(inst, 0.10, [0.10] + [0.30] * 95)
    assert b.stand == warmtepomp.BOOST
