import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import watcher


def make_listing(apt_id="apt-1", state="Unavailable"):
    return {
        "id": apt_id,
        "state": state,
        "classification": "Residential",
        "title": "2-vaerelses lejlighed",
        "monthlyRent": {"value": 10000},
        "size": {"value": 64},
        "address": {
            "street": "Amagerbrogade 238B, 2. th",
            "zipCode": "2300",
            "city": "Kobenhavn S",
        },
    }


class ProcessListingTests(unittest.TestCase):
    def test_first_run_caches_state_in_memory_only(self):
        # On the first run, process_listing records state in the in-memory dict
        # (and stays silent on Discord); persistence to disk is batched by main()
        # via save_seen_states, so nothing is written here.
        seen_states = {}
        watcher.process_listing(make_listing(), seen_states, is_first_run=True)
        self.assertEqual(seen_states, {"apt-1": "Unavailable"})


class SaveSeenStatesTests(unittest.TestCase):
    def test_round_trips_state_to_disk(self):
        original_seen_ids_file = watcher.SEEN_IDS_FILE

        with tempfile.TemporaryDirectory() as temp_dir:
            seen_file = Path(temp_dir) / "seen_ids.json"
            watcher.SEEN_IDS_FILE = str(seen_file)

            try:
                states = {"apt-1": "Available", "apt-2": "Reserved"}
                watcher.save_seen_states(states)

                self.assertTrue(seen_file.exists())
                self.assertEqual(
                    json.loads(seen_file.read_text(encoding="utf-8")),
                    states,
                )
                self.assertEqual(watcher.load_seen_states(), states)
            finally:
                watcher.SEEN_IDS_FILE = original_seen_ids_file


class CopenhagenTimeTests(unittest.TestCase):
    def test_winter_offset_is_utc_plus_one(self):
        utc = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(watcher.copenhagen_now(utc).hour, 13)

    def test_summer_offset_is_utc_plus_two(self):
        utc = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(watcher.copenhagen_now(utc).hour, 14)

    def test_dst_switch_boundary(self):
        # EU summer time begins 2026-03-29 at 01:00 UTC.
        before = datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc)
        after = datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(watcher.copenhagen_now(before).hour, 1)   # 00:30 + 1h
        self.assertEqual(watcher.copenhagen_now(after).hour, 3)    # 01:30 + 2h


class ClassifyPeriodTests(unittest.TestCase):
    @staticmethod
    def _monday(hour):
        return datetime(2026, 7, 6, hour, 0)  # a Monday

    @staticmethod
    def _saturday(hour):
        return datetime(2026, 7, 11, hour, 0)  # a Saturday

    def test_weekday_hot_window(self):
        for hour in (10, 12, 13, 14):
            self.assertEqual(watcher.classify_period(self._monday(hour)), "HOT")

    def test_weekday_warm_window(self):
        for hour in (8, 9, 15, 16):
            self.assertEqual(watcher.classify_period(self._monday(hour)), "WARM")

    def test_weekday_cool_window(self):
        for hour in (7, 17, 21):
            self.assertEqual(watcher.classify_period(self._monday(hour)), "COOL")

    def test_weekday_cold_window(self):
        for hour in (0, 3, 6, 22, 23):
            self.assertEqual(watcher.classify_period(self._monday(hour)), "COLD")

    def test_weekend_is_cool_by_day_cold_by_night(self):
        self.assertEqual(watcher.classify_period(self._saturday(12)), "COOL")
        self.assertEqual(watcher.classify_period(self._saturday(7)), "COLD")
        self.assertEqual(watcher.classify_period(self._saturday(23)), "COLD")


class PollIntervalTests(unittest.TestCase):
    def test_hot_window_uses_hot_interval(self):
        monday_peak = datetime(2026, 7, 6, 13, 0)
        self.assertEqual(
            watcher.get_poll_interval_seconds(monday_peak),
            watcher.POLL_INTERVALS["HOT"],
        )

    def test_night_uses_cold_interval(self):
        monday_night = datetime(2026, 7, 6, 3, 0)
        self.assertEqual(
            watcher.get_poll_interval_seconds(monday_night),
            watcher.POLL_INTERVALS["COLD"],
        )

    def test_intervals_speed_up_with_activity(self):
        i = watcher.POLL_INTERVALS
        self.assertLessEqual(i["HOT"], i["WARM"])
        self.assertLessEqual(i["WARM"], i["COOL"])
        self.assertLessEqual(i["COOL"], i["COLD"])

    def test_adaptive_disabled_falls_back_to_constant(self):
        original = watcher.ADAPTIVE_POLLING
        watcher.ADAPTIVE_POLLING = False
        try:
            monday_peak = datetime(2026, 7, 6, 13, 0)
            self.assertEqual(
                watcher.get_poll_interval_seconds(monday_peak, age=10),
                watcher.SLEEP_SECONDS,
            )
        finally:
            watcher.ADAPTIVE_POLLING = original


class CacheSyncedIntervalTests(unittest.TestCase):
    def test_fresh_object_waits_almost_a_full_cycle(self):
        # Age 0 -> wait TTL + margin (one full cache cycle).
        self.assertEqual(
            watcher.cache_synced_interval(0),
            watcher.CDN_CACHE_TTL_SECONDS + watcher.CACHE_SYNC_MARGIN_SECONDS,
        )

    def test_near_expiry_polls_soon_but_respects_floor(self):
        # Age just below TTL -> tiny remaining, clamped up to the min floor.
        self.assertEqual(
            watcher.cache_synced_interval(watcher.CDN_CACHE_TTL_SECONDS - 1),
            watcher.CACHE_SYNC_MIN_SECONDS,
        )

    def test_mid_cycle_targets_next_refresh(self):
        # TTL=30, margin=2, age=20 -> 30 - 20 + 2 = 12.
        expected = (
            watcher.CDN_CACHE_TTL_SECONDS - 20 + watcher.CACHE_SYNC_MARGIN_SECONDS
        )
        self.assertEqual(watcher.cache_synced_interval(20), expected)

    def test_overaged_object_clamps_to_floor(self):
        self.assertEqual(
            watcher.cache_synced_interval(watcher.CDN_CACHE_TTL_SECONDS + 100),
            watcher.CACHE_SYNC_MIN_SECONDS,
        )

    def test_never_exceeds_one_cycle(self):
        upper = watcher.CDN_CACHE_TTL_SECONDS + watcher.CACHE_SYNC_MARGIN_SECONDS
        for age in range(-5, 40):
            self.assertLessEqual(watcher.cache_synced_interval(age), upper)
            self.assertGreaterEqual(
                watcher.cache_synced_interval(age), watcher.CACHE_SYNC_MIN_SECONDS
            )


class CacheAwareTierGatingTests(unittest.TestCase):
    def test_hot_tier_with_age_syncs_to_cache(self):
        monday_peak = datetime(2026, 7, 6, 13, 0)
        self.assertEqual(
            watcher.get_poll_interval_seconds(monday_peak, age=20),
            watcher.cache_synced_interval(20),
        )

    def test_hot_tier_without_age_uses_fixed_interval(self):
        monday_peak = datetime(2026, 7, 6, 13, 0)
        self.assertEqual(
            watcher.get_poll_interval_seconds(monday_peak, age=None),
            watcher.POLL_INTERVALS["HOT"],
        )

    def test_non_cache_aware_tier_ignores_age(self):
        # COOL is not a cache-aware tier: Age must not change the interval.
        monday_evening = datetime(2026, 7, 6, 20, 0)
        self.assertEqual(watcher.classify_period(monday_evening), "COOL")
        self.assertEqual(
            watcher.get_poll_interval_seconds(monday_evening, age=5),
            watcher.POLL_INTERVALS["COOL"],
        )


class FetchApartmentsTests(unittest.TestCase):
    def test_parse_age_handles_present_and_absent(self):
        self.assertEqual(watcher._parse_age({"Age": "29"}), 29)
        self.assertIsNone(watcher._parse_age({}))
        self.assertIsNone(watcher._parse_age({"Age": "not-a-number"}))


if __name__ == "__main__":
    unittest.main()
