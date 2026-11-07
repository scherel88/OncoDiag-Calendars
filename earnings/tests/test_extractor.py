"""Offline regression tests for the public issuer browser extractor."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("issuer_extractor",ROOT/"earnings/scripts/extract_public_events.py")
mod=importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name]=mod
spec.loader.exec_module(mod)

class ParserTests(unittest.TestCase):
    def test_full_iso_with_offset(self):
        start,day,error=mod.parse_start("2026-11-10T09:45:00-05:00")
        self.assertEqual((start,day,error),("2026-11-10T09:45:00-05:00","2026-11-10",None))

    def test_dst_aware_et(self):
        self.assertEqual(mod.parse_start("Nov 7, 2026, 9:30 AM ET")[0],
                         "2026-11-07T09:30:00-05:00")
        self.assertEqual(mod.parse_start("June 10, 2026, 9:30 AM ET")[0],
                         "2026-06-10T09:30:00-04:00")

    def test_explicit_english_timezone(self):
        self.assertEqual(mod.parse_start("November 7, 2026 8:00 AM Eastern Time")[0],
                         "2026-11-07T08:00:00-05:00")

    def test_european_format(self):
        self.assertEqual(mod.parse_start("17 November 2026 14:45 GMT")[0],
                         "2026-11-17T14:45:00+00:00")

    def test_ambiguous_clock_requires_review(self):
        self.assertEqual(mod.parse_start("Nov 7, 2026 09:00 AM")[2],"timezone_missing")
        self.assertEqual(mod.parse_start("Nov 7, 2026")[2],"time_missing")

    def test_nested_json_event(self):
        data={"items":[{"eventName":"Q3 Earnings Conference Call",
                        "startDate":"2026-11-05T16:30:00-05:00",
                        "eventUrl":"/events/q3","webcastUrl":"https://streams.example/123"}]}
        out=mod.from_json("Company",data,"https://investor.company.com/events","xhr")
        self.assertEqual(len(out),1)
        self.assertTrue(out[0].review_ready)

    def test_dom_timestamp_webcast(self):
        html=('<article><h3>Q3 Earnings Conference Call</h3>'
              '<time datetime="2026-11-05T16:30:00-05:00"></time>'
              '<a href="/events/q3">Q3 Earnings Conference Call</a>'
              '<a href="https://stream.example/q3">Listen to webcast</a></article>')
        out=mod.from_html("Company",html,"https://investor.company.com/events","test")
        self.assertEqual(len(out),1)
        self.assertTrue(out[0].review_ready)

    def test_generic_listing_not_publishable(self):
        entry=mod.Candidate("Company","Q3 Earnings Conference Call","2026-11-05",
            "2026-11-05T16:30:00-05:00","https://investor.company.com/events",
            None,"https://investor.company.com/events","test","",None)
        self.assertFalse(entry.review_ready)

    def test_merge_preserves_best_evidence(self):
        a=mod.Candidate("Company","Q3 Earnings Conference Call","2026-11-05",
            None,"https://investor.company.com/events",None,
            "https://investor.company.com/events","a","","time_missing")
        b=mod.Candidate("Company","Q3 Earnings Conference Call","2026-11-05",
            "2026-11-05T16:30:00-05:00","https://investor.company.com/events/q3",
            "https://stream.example/q3","https://investor.company.com/events","b","",None)
        out=mod.merge([a,b])
        self.assertEqual(len(out),1)
        self.assertTrue(out[0].review_ready)

    def test_source_roster(self):
        r=mod.load(ROOT/"earnings/config/sources.json")
        self.assertEqual(len(r),25)
        self.assertEqual(sum(c["kind"]=="public" for c in r),23)
        self.assertEqual(sum(c["kind"]=="private" for c in r),2)
        self.assertEqual(sum(bool(c["event_urls"]) for c in r),23)
        self.assertTrue(all(c["news_urls"] for c in r))
        self.assertNotIn("Exact Sciences",[c["company"] for c in r])
        self.assertTrue(all("sec.gov" not in " ".join(c["event_urls"]+c["news_urls"]) for c in r))

if __name__=="__main__":
    unittest.main()
