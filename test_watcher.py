import json
import tempfile
import unittest
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
    def test_first_run_caches_listing_state_to_disk(self):
        original_seen_ids_file = watcher.SEEN_IDS_FILE

        with tempfile.TemporaryDirectory() as temp_dir:
            seen_file = Path(temp_dir) / "seen_ids.json"
            watcher.SEEN_IDS_FILE = str(seen_file)

            try:
                seen_states = {}
                watcher.process_listing(make_listing(), seen_states, is_first_run=True)

                self.assertEqual(seen_states, {"apt-1": "Unavailable"})
                self.assertTrue(seen_file.exists())
                self.assertEqual(
                    json.loads(seen_file.read_text(encoding="utf-8")),
                    {"apt-1": "Unavailable"},
                )
            finally:
                watcher.SEEN_IDS_FILE = original_seen_ids_file


if __name__ == "__main__":
    unittest.main()
