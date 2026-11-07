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

class RecoveryParserTests(unittest.TestCase):
    def test_public_jsonp_is_decoded_without_executing_javascript(self):
        self.assertEqual(mod.decode_public_json('callback_123({"events":[]});'),{"events":[]})
        with self.assertRaises(ValueError):
            mod.decode_public_json('alert(1); callback_123({"events":[]});')

    def test_dst_transition_ambiguous_clock_stays_pending(self):
        self.assertEqual(mod.parse_start("November 1, 2026 1:30 AM ET")[2],
                         "ambiguous_or_nonexistent_local_time")

    def test_attachment_label_is_not_a_separate_event(self):
        self.assertFalse(mod.relevant("Presentation (opens in new window) PDF 922 KB"))
        self.assertTrue(mod.relevant("Q3 Earnings Presentation"))

    def test_numeric_dates_and_dual_timezones(self):
        self.assertEqual(mod.parse_start("11/04/2026 | 1:30pm PST / 4:30pm EST")[0],
                         "2026-11-04T13:30:00-08:00")
        self.assertEqual(mod.parse_start("03/11/2026 | 9:00pm PDT / 12:00am EDT")[2],
                         "conflicting_displayed_times")

    def test_duration_range_is_not_a_conflict(self):
        self.assertEqual(mod.parse_start("November 3, 2026 8:00am - 9:00am EST"),
                         ("2026-11-03T08:00:00-05:00","2026-11-03",None))

    def test_summer_standard_timezone_requires_confirmation(self):
        self.assertEqual(mod.parse_start("June 4, 2026 10:30 am EST")[2],
                         "timezone_abbreviation_conflicts_with_season")
        self.assertEqual(mod.parse_start("June 4, 2026 10:30 am EDT")[2],None)

    def test_wordpress_numeric_date_card(self):
        html=('<div class="uk-card"><h4 class="title">Q3 Financial Earnings</h4>'
              '<p>11/04/2026 | 1:30pm PST / 4:30pm EST</p>'
              '<a href="https://stream.example/q3">Watch the webcast</a></div>')
        rows=mod.from_html("Company",html,"https://ir.example/events","test")
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0].review_ready)

    def test_webdriver_title_and_time_range(self):
        html=('<div class="wd_event"><div>November 3, 2026</div>'
              '<div>8:00am - 9:00am EST</div><div class="wd_title">Q3 Earnings Call</div>'
              '<a href="/events?item=37">More</a></div>')
        rows=mod.from_html("Company",html,"https://ir.example/events","test")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].event_url,"https://ir.example/events?item=37")
        self.assertTrue(rows[0].review_ready)

    def test_repeated_same_date_does_not_discard_card(self):
        html=('<div class="views-row"><h3>Q3 Earnings Conference Call</h3>'
              '<p>November 5, 2026</p><p>November 5, 2026 at 4:30 PM ET</p>'
              '<a href="/events/q3">Event details</a></div>')
        rows=mod.from_html("Company",html,"https://ir.example/events","test")
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0].review_ready)

    def test_different_event_dates_do_not_leak_between_cards(self):
        html=('<div><h2>Investor Presentations</h2>'
              '<p>November 5, 2026 4:30 PM ET</p>'
              '<p>November 10, 2026 9:00 AM ET</p></div>')
        self.assertEqual(mod.from_html("Company",html,"https://ir.example/events","test"),[])

    def test_google_calendar_timestamp_is_local_evidence(self):
        from urllib.parse import urlencode
        link="https://www.google.com/calendar/render?"+urlencode({
            "action":"TEMPLATE","text":"UBS Global Healthcare Conference",
            "dates":"20261110T160000Z/20261110T170000Z",
            "details":"Event Details: https://ir.example/events/ubs\n\nWebcast: https://stream.example/ubs"})
        event=mod.from_calendar_link("Company",link,"https://ir.example/events","test")
        self.assertEqual(event.start,"2026-11-10T16:00:00+00:00")
        self.assertEqual(event.webcast_url,"https://stream.example/ubs")
        self.assertTrue(event.review_ready)

    def test_conflicting_timestamps_are_not_review_ready(self):
        rows=[mod.Candidate("Company","Q3 Earnings Call","2026-11-05",start,
              "https://ir.example/events/q3",None,"https://ir.example/events",
              "test","",None) for start in
              ("2026-11-05T16:30:00-05:00","2026-11-05T17:30:00-05:00")]
        event=mod.merge(rows)[0]
        self.assertFalse(event.review_ready)
        self.assertIn("conflicting_source_timestamps",event.conflicts)
        self.assertEqual(len(event.evidence),2)

    def test_equivalent_offsets_are_not_conflicts(self):
        rows=[mod.Candidate("Company","Q3 Earnings Call","2026-11-05",start,
              "https://ir.example/events/q3",None,"https://ir.example/events",
              "test","",None) for start in
              ("2026-11-05T16:30:00-05:00","2026-11-05T21:30:00+00:00")]
        event=mod.merge(rows)[0]
        self.assertTrue(event.review_ready)

    def test_issuer_siblings_but_not_other_vendor_tenants(self):
        self.assertTrue(mod.same_issuer("https://www.example.com/a","https://ir.example.com/"))
        self.assertFalse(mod.same_issuer("https://wrong.gcs-web.com/","https://issuer.gcs-web.com/"))
        self.assertFalse(mod.same_issuer("https://example.com.evil.test/","https://ir.example.com/"))

    def test_newsletter_captcha_does_not_hide_real_page(self):
        self.assertFalse(mod.is_challenge("<html><title>Events</title><p>Upcoming events</p>"
            "<form>The captcha test has failed. Please try again.</form></html>"))
        self.assertTrue(mod.is_challenge("<html><title>Just a moment...</title>"
            "<p>Verify you are human</p></html>"))

    def test_news_article_json_ld_is_not_an_event(self):
        obj={"@type":"NewsArticle","headline":"Q3 Earnings Conference Call",
             "name":"Q3 Earnings Conference Call","datePublished":"2026-10-15T09:00:00Z"}
        self.assertEqual(mod.from_json("Company",obj,"https://ir.example/news","test:json-ld"),[])

    def test_rss_publication_timestamp_never_becomes_call_time(self):
        feed=('<rss><channel><item><title>Q3 Earnings Conference Call</title>'
              '<link>https://ir.example/news/q3</link>'
              '<pubDate>Thu, 15 Oct 2026 09:00:00 GMT</pubDate>'
              '<description>We will announce the call date soon.</description>'
              '</item></channel></rss>')
        self.assertEqual(mod.from_feed("Company",feed,"https://ir.example/rss"),[])

    def test_rss_explicit_event_time(self):
        feed=('<rss><channel><item><title>Q3 Earnings Conference Call</title>'
              '<link>https://ir.example/news/q3</link><description><![CDATA['
              '<p>The call is November 5, 2026 at 4:30 PM ET.</p>'
              ']]></description></item></channel></rss>')
        rows=mod.from_feed("Company",feed,"https://ir.example/rss")
        self.assertEqual(rows[0].start,"2026-11-05T16:30:00-05:00")

    def test_xml_entities_rejected(self):
        with self.assertRaises(ValueError):
            mod.xml_root('<!DOCTYPE rss [<!ENTITY x "boom">]><rss>&x;</rss>')

    def test_release_date_and_call_in_adjacent_paragraphs(self):
        html=('<h1>Company to Report Q3 Financial Results</h1>'
              '<p>On October 15, 2026, we announced results will be reported on Monday, November 2, 2026.</p>'
              '<p>The company will host a conference call at 8:45 a.m ET.</p>'
              '<p><a href="https://stream.example/q3">Webcast link</a></p>')
        rows=mod.from_release("Company",html,"https://ir.example/news/q3")
        self.assertEqual(rows[0].start,"2026-11-02T08:45:00-05:00")
        self.assertTrue(rows[0].review_ready)

    def test_release_timestamp_is_not_carried_into_call(self):
        html=('<h1>Company Q3 Earnings Call</h1><p>October 15, 2026</p>'
              '<p>The conference call starts at 4:30 PM ET; the date will be announced later.</p>')
        self.assertEqual(mod.from_release("Company",html,"https://ir.example/news/q3"),[])

    def test_linked_ical_with_tzid(self):
        text=("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
              "UID:test\r\nSUMMARY:Q3 Earnings Call\r\n"
              "DTSTART;TZID=America/New_York:20261105T163000\r\n"
              "URL:https://ir.example/events/q3\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        rows=mod.from_ical("Company",text,"https://ir.example/q3.ics","https://ir.example/events")
        self.assertEqual(rows[0].start,"2026-11-05T16:30:00-05:00")
        self.assertTrue(rows[0].review_ready)

    def test_floating_ical_time_requires_timezone(self):
        text=("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
              "UID:test\r\nSUMMARY:Q3 Earnings Call\r\n"
              "DTSTART:20261105T163000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        rows=mod.from_ical("Company",text,"https://ir.example/q3.ics","https://ir.example/events")
        self.assertIsNone(rows[0].start)
        self.assertEqual(rows[0].missing,"timezone_missing")

    def test_invalid_date_does_not_abort_other_cards(self):
        html=('<article><h3>Q3 Earnings Call</h3><p>November 32, 2026 4:30 PM ET</p></article>'
              '<article><h3>Q4 Earnings Call</h3><p>December 5, 2026 4:30 PM ET</p></article>')
        rows=mod.from_html("Company",html,"https://ir.example/events","test")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].event_date,"2026-12-05")


class HTTPRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_singleflight_coalesces_requests(self):
        import asyncio
        import httpx
        calls=[]
        async def handler(request):
            calls.append(request.url)
            await asyncio.sleep(0.01)
            return httpx.Response(200,text="<p>Public issuer content</p>")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            http=mod.PublicHTTP(client)
            rows=await asyncio.gather(*(http.fetch("https://ir.example/events") for _ in range(8)))
            self.assertEqual(len(calls),1)
            self.assertTrue(all(r.status==200 for r in rows))

    async def test_503_retry_and_403_not_retried(self):
        import httpx
        calls=[]
        async def handler(request):
            calls.append(request.url.path)
            if request.url.path=="/blocked":
                return httpx.Response(403)
            return httpx.Response(503 if calls.count("/events")==1 else 200,text="<p>Events</p>")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            http=mod.PublicHTTP(client)
            self.assertEqual((await http.fetch("https://ir.example/events")).status,200)
            self.assertEqual((await http.fetch("https://ir.example/blocked")).status,403)
            self.assertEqual(calls.count("/events"),2)
            self.assertEqual(calls.count("/blocked"),1)

    async def test_retry_after_cools_entire_host(self):
        import httpx
        calls=[]
        async def handler(request):
            calls.append(str(request.url))
            return httpx.Response(429,headers={"Retry-After":"60"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            http=mod.PublicHTTP(client)
            await http.fetch("https://ir.example/events")
            res=await http.fetch("https://ir.example/other")
            self.assertEqual(res.error,"host_rate_limited")
            self.assertEqual(len(calls),1)

    async def test_etag_revalidation_and_no_stale_on_error(self):
        import httpx,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            async def fresh(request):
                return httpx.Response(200,text="<p>Events</p>",headers={"ETag":'"v1"'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(fresh)) as client:
                await mod.PublicHTTP(client,Path(d)).fetch("https://ir.example/events")
            async def unchanged(request):
                self.assertEqual(request.headers["if-none-match"],'"v1"')
                return httpx.Response(304)
            async with httpx.AsyncClient(transport=httpx.MockTransport(unchanged)) as client:
                result=await mod.PublicHTTP(client,Path(d)).fetch("https://ir.example/events")
                self.assertEqual(result.cache,"revalidated")
                self.assertEqual(result.text,"<p>Events</p>")
            async def blocked(request):
                return httpx.Response(403)
            async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
                result=await mod.PublicHTTP(client,Path(d)).fetch("https://ir.example/events")
                self.assertEqual(result.text,"")
                self.assertEqual(result.status,403)

    async def test_oversized_body_is_rejected(self):
        import httpx
        async def handler(request):
            return httpx.Response(200,content=b"x"*(mod.MAX_BODY+1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await mod.PublicHTTP(client).fetch("https://ir.example/events")
            self.assertEqual(result.error,"body_too_large")

    async def test_blocked_primary_recovers_via_official_news(self):
        import httpx
        async def handler(request):
            if request.url.path=="/events":
                return httpx.Response(403)
            if request.url.path=="/news":
                return httpx.Response(200,text='<main><a href="/news/q3">Q3 Earnings Call</a></main>')
            return httpx.Response(200,text='<h1>Q3 Earnings Call</h1><p>November 5, 2026 at 4:30 PM ET.</p>')
        company={"company":"TestCo","event_urls":["https://ir.example/events"],
                 "news_urls":["https://ir.example/news"]}
        state={"log":{"http_attempts":[],"errors":[],"detail_pages":0},
               "found":[],"documents":{},"primary_readable":False}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await mod.http_sources(mod.PublicHTTP(client),company,[2026],3,state)
        self.assertEqual(len(state["found"]),1)
        self.assertEqual(state["found"][0].start,"2026-11-05T16:30:00-05:00")
        self.assertFalse(state["primary_readable"])

    async def test_deadline_keeps_already_extracted_events(self):
        import asyncio,httpx
        async def handler(request):
            if request.url.path=="/events":
                return httpx.Response(200,text='<article><h3>Q3 Earnings Call</h3>'
                    '<p>November 5, 2026</p><a href="/events/q3">Q3 Earnings Call</a></article>')
            await asyncio.sleep(1)
            return httpx.Response(200,text="")
        company={"company":"TestCo","event_urls":["https://ir.example/events"],"news_urls":[]}
        state={"log":{"http_attempts":[],"errors":[],"detail_pages":0},
               "found":[],"documents":{},"primary_readable":False}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            http=mod.PublicHTTP(client)
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(0.04):
                    await mod.http_sources(http,company,[2026],3,state)
            self.assertEqual(len(state["found"]),1)
            self.assertEqual(state["found"][0].event_date,"2026-11-05")
            await http.close()


if __name__=="__main__":
    unittest.main()

