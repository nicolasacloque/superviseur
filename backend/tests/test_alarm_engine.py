"""Moteur d'alarmes : chaque type de règle, la machine à états et l'absence de doublons."""

import uuid

from app.alarms.engine import AlarmEngine
from app.alarms.store import LatestValue
from app.common.bus import CHANNEL_ALARM_EVENT
from app.common.models import AlarmState as S
from tests.alarm_fakes import T0, build, make_rule


def only_state(engine: AlarmEngine) -> S:
    (state,) = engine.states().values()
    return state


class TestHighRule:
    async def test_raises_once_when_the_value_crosses_the_threshold(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()

        await engine.on_value(rule.point_id, 25.0)
        assert store.events == {} and rec.published == []  # rien sous le seuil

        await engine.on_value(rule.point_id, 30.5)
        (event,) = store.events.values()
        assert (event.state, event.raised_value, event.raised_at) == ("active_unacked", 30.5, T0)
        assert rec.transitions() == ["raised"] and rec.notified() == ["raised"]
        channel, payload = rec.published[0]
        assert channel == CHANNEL_ALARM_EVENT
        assert payload["event"]["state"] == "active_unacked" and payload["event"]["value"] == 30.5
        assert (
            payload["event"]["path"] == "Site/CTA-1/Temp"
            and payload["event"]["severity"] == "critical"
        )

    async def test_no_duplicate_notification_while_the_condition_lasts(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        for value in [30, 31, 32, 29, 33, 30.1] * 20:  # 120 mesures au-dessus du seuil
            await engine.on_value(rule.point_id, value)
        assert rec.notified() == ["raised"] and len(store.events) == 1
        assert len(rec.published) == 1

    async def test_full_lifecycle_raise_acknowledge_clear(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, clocks = build(rule)
        operator = uuid.uuid4()
        store.logins[operator] = "olivia"
        await engine.load()

        await engine.on_value(rule.point_id, 30)
        (event,) = store.events.values()
        clocks.advance(60)
        result = await engine.acknowledge(event.id, operator)
        assert result.ok and (event.state, event.acked_by) == ("active_acked", "olivia")
        assert event.acked_at == clocks.wall()

        clocks.advance(60)
        await engine.on_value(rule.point_id, 22)
        assert event.state == "normal" and event.cleared_at == clocks.wall()
        assert rec.transitions() == ["raised", "acked", "normal"]
        assert rec.notified() == ["raised", "acked", "normal"]  # une par changement d'état
        assert only_state(engine) is S.NORMAL

    async def test_cleared_before_acknowledgement_then_acknowledged(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        operator = uuid.uuid4()
        store.logins[operator] = "olivia"
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        (event,) = store.events.values()

        await engine.on_value(rule.point_id, 20)  # la condition disparaît
        assert event.state == "cleared_unacked" and event.cleared_at is not None
        assert (await engine.acknowledge(event.id, operator)).ok
        assert event.state == "normal" and event.acked_by == "olivia"
        assert rec.transitions() == ["raised", "cleared", "acked"]

    async def test_the_same_alarm_reactivates_when_the_condition_returns_before_the_ack(
        self,
    ) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        await engine.on_value(rule.point_id, 20)
        await engine.on_value(rule.point_id, 31)
        assert len(store.events) == 1  # pas de seconde alarme ouverte
        (event,) = store.events.values()
        assert event.state == "active_unacked" and event.cleared_at is None
        assert rec.transitions() == ["raised", "cleared", "raised"]

    async def test_a_new_occurrence_after_the_alarm_was_closed_is_a_new_event(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        operator = uuid.uuid4()
        store.logins[operator] = "o"
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        (first,) = store.events.values()
        await engine.acknowledge(first.id, operator)
        await engine.on_value(rule.point_id, 20)
        await engine.on_value(rule.point_id, 40)
        assert len(store.events) == 2 and first.state == "normal"
        assert sum(1 for e in store.events.values() if e.state != "normal") == 1

    async def test_hysteresis_keeps_the_alarm_active_until_the_value_falls_far_enough(self) -> None:
        rule = make_rule("high", 28, hysteresis=2)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 29)  # déclenche
        await engine.on_value(rule.point_id, 27)  # sous le seuil mais dans l'hystérésis
        await engine.on_value(rule.point_id, 28.5)
        assert rec.transitions() == ["raised"]  # ni oscillation ni notification
        await engine.on_value(rule.point_id, 25.9)  # retombée
        assert rec.transitions() == ["raised", "cleared"]

    async def test_a_reading_without_value_neither_raises_nor_clears(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        await engine.on_value(rule.point_id, None, "comm_lost")  # point injoignable
        assert rec.transitions() == ["raised"]  # l'alarme n'est pas effacée
        (event,) = store.events.values()
        assert event.state == "active_unacked"


class TestDelay:
    async def test_the_alarm_waits_for_the_delay_before_raising(self) -> None:
        rule = make_rule("high", 28, delay_s=30)
        engine, store, rec, clocks = build(rule)
        await engine.load()

        await engine.on_value(rule.point_id, 30)
        assert store.events == {} and only_state(engine) is S.PENDING
        clocks.advance(29)
        await engine.tick()
        assert store.events == {}  # pas encore
        clocks.advance(1)
        await engine.tick()
        (event,) = store.events.values()
        assert event.state == "active_unacked" and event.raised_at == clocks.wall()
        assert rec.notified() == ["raised"]

    async def test_a_short_excursion_never_raises_and_never_notifies(self) -> None:
        rule = make_rule("high", 28, delay_s=30)
        engine, store, rec, clocks = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        clocks.advance(10)
        await engine.on_value(rule.point_id, 20)  # revenu avant 30 s
        clocks.advance(60)
        await engine.tick()
        assert store.events == {} and rec.published == [] and rec.notifications == []
        assert only_state(engine) is S.NORMAL

    async def test_the_delay_counts_from_the_first_excursion_not_the_last_value(self) -> None:
        rule = make_rule("high", 28, delay_s=30)
        engine, store, rec, clocks = build(rule)
        await engine.load()
        for _ in range(6):
            await engine.on_value(rule.point_id, 31)
            clocks.advance(5)
        await engine.on_value(rule.point_id, 31)  # 30 s après la première mesure
        assert rec.transitions() == ["raised"]


class TestLowAndStateRules:
    async def test_low_rule(self) -> None:
        rule = make_rule("low", 5, hysteresis=1)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 4.0)
        await engine.on_value(rule.point_id, 5.5)  # dans l'hystérésis : maintenue
        await engine.on_value(rule.point_id, 6.0)
        assert rec.transitions() == ["raised", "cleared"]

    async def test_state_rule_on_a_binary_fault(self) -> None:
        rule = make_rule("state", 1)  # défaut = 1
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 0.0)
        await engine.on_value(rule.point_id, 1.0)
        await engine.on_value(rule.point_id, 1.0)
        await engine.on_value(rule.point_id, 0.0)
        assert rec.transitions() == ["raised", "cleared"]


class TestStaleRule:
    async def test_raises_when_no_new_value_arrives_within_the_limit(self) -> None:
        rule = make_rule("stale", 300)
        engine, store, rec, clocks = build(rule)
        await engine.load()
        clocks.advance(299)
        await engine.tick()
        assert rec.published == []
        clocks.advance(2)
        await engine.tick()
        await engine.tick()  # plusieurs ticks : une seule alarme
        assert rec.transitions() == ["raised"] and rec.notified() == ["raised"]

    async def test_a_steady_flow_of_values_never_raises(self) -> None:
        rule = make_rule("stale", 300)
        engine, _, rec, clocks = build(rule)
        await engine.load()
        for _ in range(20):
            clocks.advance(200)
            await engine.on_value(rule.point_id, 21.0)  # même valeur : pas figée, elle est reçue
            await engine.tick()
        assert rec.published == []

    async def test_a_fresh_value_clears_a_stale_alarm(self) -> None:
        rule = make_rule("stale", 60)
        engine, _, rec, clocks = build(rule)
        await engine.load()
        clocks.advance(120)
        await engine.tick()
        await engine.on_value(rule.point_id, 21.0)
        assert rec.transitions() == ["raised", "cleared"]

    async def test_comm_lost_readings_do_not_count_as_fresh(self) -> None:
        rule = make_rule("stale", 60)
        engine, _, rec, clocks = build(rule)
        await engine.load()
        for _ in range(4):
            clocks.advance(30)
            await engine.on_value(rule.point_id, None, "comm_lost")
            await engine.tick()
        assert rec.transitions() == ["raised"]

    async def test_the_age_starts_from_the_last_stored_value_at_startup(self) -> None:
        rule = make_rule("stale", 300)
        old = LatestValue(T0, 20.0, "ok")  # dernière valeur en base : il y a 10 minutes
        engine, store, rec, clocks = build(rule, latest={rule.point_id: old})
        clocks.advance(600)
        await engine.load()
        await engine.tick()
        assert rec.transitions() == ["raised"]


class TestCommLostRule:
    async def test_follows_the_device_online_status(self) -> None:
        rule = make_rule("comm_lost", None)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_device_status(rule.device_id, False)
        await engine.on_device_status(rule.device_id, False)
        await engine.on_device_status(rule.device_id, True)
        assert rec.transitions() == ["raised", "cleared"]

    async def test_other_devices_do_not_matter(self) -> None:
        rule = make_rule("comm_lost", None)
        engine, _, rec, _ = build(rule)
        await engine.load()
        await engine.on_device_status(uuid.uuid4(), False)
        assert rec.published == []

    async def test_a_device_already_offline_at_startup_raises_immediately(self) -> None:
        rule = make_rule("comm_lost", None)
        engine, store, rec, _ = build(rule)
        store.online[rule.device_id] = False
        await engine.load()
        assert rec.transitions() == ["raised"]


class TestBacnetEventRule:
    async def test_follows_the_controller_event_state(self) -> None:
        rule = make_rule("bacnet_event", None)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_bacnet_event(1001, "analog-input", 1, "high-limit")
        await engine.on_bacnet_event(1001, "analog-input", 1, "offnormal")  # toujours anormal
        await engine.on_bacnet_event(1001, "analog-input", 1, "normal")
        assert rec.transitions() == ["raised", "cleared"]

    async def test_events_of_other_objects_are_ignored(self) -> None:
        rule = make_rule("bacnet_event", None)
        engine, _, rec, _ = build(rule)
        await engine.load()
        await engine.on_bacnet_event(1001, "analog-input", 2, "high-limit")
        await engine.on_bacnet_event(1002, "analog-input", 1, "high-limit")
        assert rec.published == []


class TestAcknowledgement:
    async def test_only_alarms_waiting_for_an_ack_can_be_acknowledged(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        operator = uuid.uuid4()
        await engine.load()
        assert not (await engine.acknowledge(uuid.uuid4(), operator)).ok  # inconnue
        await engine.on_value(rule.point_id, 30)
        (event,) = store.events.values()
        assert (await engine.acknowledge(event.id, operator)).ok
        second = await engine.acknowledge(event.id, operator)
        assert not second.ok and "active_acked" in (second.error or "")
        assert rec.notified() == ["raised", "acked"]  # le second essai n'a rien notifié

    async def test_a_failed_persistence_leaves_the_alarm_acknowledgeable(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        (event,) = store.events.values()
        store.fail = True
        assert not (await engine.acknowledge(event.id, uuid.uuid4())).ok
        store.fail = False
        assert (await engine.acknowledge(event.id, uuid.uuid4())).ok
        assert rec.notified() == ["raised", "acked"]


class TestNotifications:
    async def test_a_rule_without_channel_still_records_and_publishes(self) -> None:
        rule = make_rule("high", 28, notify=[])
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        assert (
            len(store.events) == 1 and rec.transitions() == ["raised"] and rec.notifications == []
        )

    async def test_the_notification_carries_the_rule_channels_and_context(self) -> None:
        rule = make_rule("high", 28, notify=["email:a@x.fr", "webhook:https://h.example/x"])
        engine, _, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30.5)
        (notification,) = rec.notifications
        assert notification.channels == ("email:a@x.fr", "webhook:https://h.example/x")
        assert "30.5 °C" in notification.body and "Seuil : 28 °C" in notification.body


class TestPersistenceAndRestart:
    async def test_a_restarted_engine_resumes_open_alarms_without_new_notifications(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, clocks = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        assert rec.notified() == ["raised"]

        # Redémarrage : nouveau moteur, même base.
        again, _, rec2, _ = build(rule)
        again._store = store
        await again.load()
        assert again.states() == {rule.id: S.ACTIVE_UNACKED}
        await again.on_value(rule.point_id, 31)  # la condition dure : rien à signaler
        assert rec2.published == [] and rec2.notifications == []  # pas de doublon après redémarrage
        await again.on_value(rule.point_id, 20)
        assert rec2.transitions() == ["cleared"]

    async def test_startup_catches_up_with_values_already_in_the_database(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule, latest={rule_point(rule): LatestValue(T0, 35.0, "ok")})
        await engine.load()
        assert rec.transitions() == ["raised"]

    async def test_startup_closes_an_acknowledged_alarm_whose_condition_ended_while_down(
        self,
    ) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        operator = uuid.uuid4()
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        (event,) = store.events.values()
        await engine.acknowledge(event.id, operator)

        again, _, rec2, _ = build(rule, latest={rule.point_id: LatestValue(T0, 20.0, "ok")})
        again._store = store
        again._store.latest = {rule.point_id: LatestValue(T0, 20.0, "ok")}
        await again.load()
        assert event.state == "normal" and rec2.transitions() == ["normal"]

    async def test_reloading_keeps_the_state_of_existing_rules(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        await engine.load()  # rechargement périodique
        assert engine.states() == {rule.id: S.ACTIVE_UNACKED}
        assert rec.notified() == ["raised"]

    async def test_a_modified_threshold_applies_to_the_next_value(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 25)
        rule.threshold = 20.0
        await engine.load()
        await engine.on_value(rule.point_id, 25)
        assert rec.transitions() == ["raised"]

    async def test_disabling_a_rule_closes_its_open_alarm(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        (event,) = store.events.values()
        store.rules.clear()  # règle désactivée : elle n'est plus chargée
        await engine.load()
        assert event.state == "normal" and event.cleared_at is not None
        assert engine.states() == {}
        assert rec.published[-1][1]["transition"] == "normal"

    async def test_new_rules_are_picked_up_on_reload(self) -> None:
        engine, store, rec, _ = build()
        await engine.load()
        rule = make_rule("high", 28)
        store.rules.append(rule)
        await engine.load()
        await engine.on_value(rule.point_id, 30)
        assert rec.transitions() == ["raised"]


class TestDatabaseFailure:
    async def test_nothing_is_published_or_notified_when_the_database_is_down(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        store.fail = True
        await engine.on_value(rule.point_id, 30)
        assert rec.published == [] and rec.notifications == [] and store.events == {}
        # Transition annulée : la règle reste en attente et la levée sera retentée.
        assert only_state(engine) is S.PENDING

    async def test_the_alarm_is_raised_exactly_once_after_recovery(self) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        store.fail = True
        await engine.on_value(rule.point_id, 30)
        await engine.on_value(rule.point_id, 31)
        store.fail = False
        await engine.on_value(rule.point_id, 32)
        await engine.on_value(rule.point_id, 33)
        assert rec.notified() == ["raised"] and len(store.events) == 1


class TestSeveralRulesOnOnePoint:
    async def test_high_and_low_rules_are_independent(self) -> None:
        point = uuid.uuid4()
        high = make_rule("high", 28, point_id=point)
        low = make_rule("low", 5, point_id=point)
        engine, store, rec, _ = build(high, low)
        await engine.load()
        await engine.on_value(point, 30)
        await engine.on_value(point, 2)
        assert rec.transitions() == ["raised", "cleared", "raised"]
        states = engine.states()
        assert states[high.id] is S.CLEARED_UNACKED and states[low.id] is S.ACTIVE_UNACKED


def rule_point(rule: object) -> uuid.UUID:
    return rule.point_id  # type: ignore[attr-defined, no-any-return]
