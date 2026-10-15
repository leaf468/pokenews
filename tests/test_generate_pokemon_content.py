"""최신성·중복 검증 로직 테스트 (네트워크/OpenAI 호출 없음).

실행: python -m unittest discover tests
"""

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_pokemon_content as g  # noqa: E402


def page(text: str, published: date | None = None) -> dict:
    return {"ok": True, "error": "", "text": text, "image_url": "", "published_date": published}


class FreshnessTest(unittest.TestCase):
    today = date(2026, 10, 7)

    def check(self, text, published=None, **candidate):
        ok, _, base_date = g.check_freshness(candidate, page(text, published), self.today)
        return ok, base_date

    def test_recent_article_passes_on_measured_date(self):
        self.assertEqual(self.check("본문", date(2026, 10, 2)), (True, "2026-10-02"))

    def test_old_article_fails_even_if_model_claims_today(self):
        ok, _ = self.check("본문", date(2026, 9, 17), published_date="2026-10-07")
        self.assertFalse(ok)

    def test_claimed_publish_date_must_appear_on_page(self):
        self.assertFalse(self.check("날짜 없는 본문", published_date="2026-10-06")[0])
        self.assertTrue(self.check("등록 2026.10.06 본문", published_date="2026-10-06")[0])

    def test_last_years_product_page_fails(self):
        # 실제 사례: 2025년 발매 제품 페이지를 올해 발매 소식으로 올림
        ok, _ = self.check("스타터 세트 MEGA 발매일 2025-10-24 가격 12,000원", event_date="2026-10-10")
        self.assertFalse(ok)

    def test_last_years_event_fails_on_weekday(self):
        # 실제 사례: 2025년 10월 13일(월) 예약 공지를 2026년 일정으로 올림 (2026-10-13은 화요일)
        text = "일정 1회차 : 2025년 10월 18일(토) 온라인 예약 10월 13일(월) 18:00 시작"
        self.assertFalse(self.check(text, event_date="2026-10-13")[0])

    def test_yearless_date_on_stale_page_fails(self):
        text = "작성 2025년 10월 1일. 예약은 10월 13일 18:00 시작"
        self.assertFalse(self.check(text, event_date="2026-10-13")[0])

    def test_yearless_date_with_matching_weekday_passes(self):
        text = "온라인 예약은 10월 13일(화) 18:00부터"
        self.assertEqual(self.check(text, event_date="2026-10-13"), (True, "2026-10-13"))

    def test_upcoming_event_with_full_date_passes(self):
        text = "2026年10月16日(金)より発売"
        self.assertEqual(self.check(text, event_date="2026-10-16"), (True, "2026-10-16"))

    def test_far_future_or_past_event_fails(self):
        text = "개최 일시 2027년 1월 23일(토) / 지난 행사 2026년 9월 12일(토)"
        self.assertFalse(self.check(text, event_date="2027-01-23")[0])
        self.assertFalse(self.check(text, event_date="2026-09-12")[0])


class PostTextTest(unittest.TestCase):
    today = date(2026, 10, 7)

    def test_wrong_weekday_in_post_is_caught(self):
        self.assertTrue(g.find_weekday_error("2026년 10월 13일(월) 18:00 예약 시작", self.today))
        self.assertTrue(g.find_weekday_error("10월 13일(월) 예약 시작", self.today))

    def test_correct_weekday_is_fine(self):
        self.assertEqual(g.find_weekday_error("10월 13일(화)부터, 2027년 1월 23일(토) 개최", self.today), "")


class DuplicateTest(unittest.TestCase):
    today = date(2026, 10, 8)
    history = [
        {
            "topic": "Pokémon Team Masters 2027 in SEOUL 개최·응모 9/28 시작",
            "source_url": "https://www.pokemonkorea.co.kr/news/2/21301?utm_source=openai",
            "posted_at": "2026-09-29T12:48:00+09:00",
        },
        {
            "topic": "Pokémon TCG: 30th Celebration Mini Tin — 공식 출시일 2026-10-02",
            "source_url": "https://www.pokemon.com/us/pokemon-tcg/product-gallery/30th-celebration-mini-tin",
            "posted_at": "2026-09-21T18:32:00+09:00",
        },
    ]

    def test_same_url_is_duplicate(self):
        candidate = {"topic": "팀전 대회 서울 개최", "source_url": "https://pokemonkorea.co.kr/news/2/21301/"}
        self.assertIn("URL", g.find_duplicate(candidate, self.history, self.today))

    def test_similar_topic_is_duplicate(self):
        candidate = {"topic": "Pokémon Team Masters 2027 in SEOUL — 팀전 형식 신규 대회 서울 개최(2027-01-23~24)"}
        self.assertIn("유사", g.find_duplicate(candidate, self.history, self.today))

    def test_different_product_in_same_series_is_not_duplicate(self):
        candidate = {"topic": "Pokémon TCG: 30th Celebration 부스터 번들(6팩) 2026-10-02 발매"}
        self.assertEqual(g.find_duplicate(candidate, self.history, self.today), "")

    def test_topic_outside_window_is_allowed_but_url_is_not(self):
        later = date(2027, 3, 1)
        candidate = {"topic": "Pokémon Team Masters 2027 in SEOUL 개최·응모 9/28 시작"}
        self.assertEqual(g.find_duplicate(candidate, self.history, later), "")
        candidate["source_url"] = "https://www.pokemonkorea.co.kr/news/2/21301"
        self.assertIn("URL", g.find_duplicate(candidate, self.history, later))


class FreshItemsTest(unittest.TestCase):
    now = datetime(2026, 10, 13, 22, 0, tzinfo=g.KST)

    def item(self, title, url, hours_ago=None, day=None):
        published = self.now - timedelta(hours=hours_ago) if hours_ago is not None else day
        return {"kind": "news", "source": "테스트", "title": title, "url": url, "summary": "",
                "image": "", "published": published, "dated_by_day": day is not None}

    def urls(self, items, mode, state=None):
        state = state or {"posts": [], "checked": {}}
        return [i["url"] for i in g.fresh_items(items, state, mode, self.now)]

    def test_window_depends_on_mode(self):
        items = [self.item("세 시간 전 소식 가나다라", "https://a.kr/1", 3),
                 self.item("이틀 전 소식 마바사아", "https://a.kr/2", 48),
                 self.item("나흘 전 소식 자차카타", "https://a.kr/3", 96)]
        self.assertEqual(self.urls(items, "breaking"), ["https://a.kr/1"])
        self.assertEqual(self.urls(items, "scheduled"), ["https://a.kr/1", "https://a.kr/2"])

    def test_social_posts_must_be_within_a_day_in_any_mode(self):
        old = self.item("어제 낮에 올라온 스레드 글 가나다", "https://www.threads.com/@a/post/1", 30)
        new = self.item("방금 올라온 스레드 입고 글 라마바", "https://www.threads.com/@a/post/2", 2)
        old["kind"] = new["kind"] = "threads"
        old["summary"] = new["summary"] = "GS25 강남점 포켓몬카드 30주년 확장팩 오늘 오후 3시 입고, 1인 2팩 제한 " * 4
        self.assertEqual(self.urls([new, old], "scheduled"), [new["url"]])

    def test_short_social_posts_are_skipped(self):
        short = self.item("안녕하세요!", "https://www.threads.com/@a/post/3", 1)
        short["kind"], short["summary"] = "threads", "안녕하세요! 포켓몬카드 있나요?"
        self.assertEqual(self.urls([short], "breaking"), [])

    def test_day_only_dates_compare_by_day(self):
        yesterday = datetime(2026, 10, 12, tzinfo=g.KST)
        last_week = datetime(2026, 10, 5, tzinfo=g.KST)
        items = [self.item("어제 올라온 공식 공지", "https://a.kr/1", day=yesterday),
                 self.item("지난주 올라온 공식 공지", "https://a.kr/2", day=last_week)]
        self.assertEqual(self.urls(items, "breaking"), ["https://a.kr/1"])

    def test_posted_and_checked_items_are_skipped(self):
        items = [self.item("GS25 포켓몬 30주년 카드 출시", "https://a.kr/1", 1),
                 self.item("이미 속보 판정한 글 가나다", "https://a.kr/2", 1),
                 self.item("처음 보는 새 소식 라마바", "https://a.kr/3", 1)]
        state = {"posts": [{"topic": "옛 소재", "source_url": "https://www.a.kr/1/", "posted_at": "2026-10-13T09:00:00+09:00"}],
                 "checked": {"a.kr/2": "2026-10-13T21:40:00+09:00"}}
        self.assertEqual(self.urls(items, "breaking", state), ["https://a.kr/3"])
        self.assertEqual(self.urls(items, "scheduled", state), ["https://a.kr/2", "https://a.kr/3"])


class VerifyPostTest(unittest.TestCase):
    now = datetime(2026, 10, 14, 9, 0, tzinfo=g.KST)
    news_a = g.make_source("뉴스A", "https://www.a-news.kr/1", "news", "본문")
    news_a2 = g.make_source("뉴스A 모바일", "https://m.a-news.kr/2", "news", "본문")
    news_b = g.make_source("뉴스B", "https://b-news.kr/9", "news", "본문")
    official = g.make_source("포켓몬코리아", "https://pokemonkorea.co.kr/news/2/1", "official", "본문")

    def verdict(self, sources, claims, core):
        original = g.ask_json
        g.ask_json = lambda system, user: {"claims": claims, "core": {"claim": "핵심", "supported_by": core}}
        try:
            return g.verify_post(["포스트"], sources, self.now)
        finally:
            g.ask_json = original

    def claim(self, supported_by, problem="", severity=""):
        return {"claim": "10월 24일 입고", "supported_by": supported_by, "problem": problem, "severity": severity}

    def test_two_independent_sources_pass(self):
        self.assertTrue(self.verdict([self.news_a, self.news_b], [self.claim([0, 1])], [0, 1])["ok"])

    def test_single_news_source_fails(self):
        self.assertFalse(self.verdict([self.news_a, self.news_b], [self.claim([0])], [0])["ok"])

    def test_same_site_twice_is_not_independent(self):
        self.assertFalse(self.verdict([self.news_a, self.news_a2], [self.claim([0, 1])], [0, 1])["ok"])

    def test_official_source_alone_passes(self):
        self.assertTrue(self.verdict([self.official], [self.claim([0])], [0])["ok"])

    def test_unsupported_conflicting_or_distorted_claims_fail(self):
        sources = [self.news_a, self.news_b]
        self.assertFalse(self.verdict(sources, [self.claim([0, 1]), self.claim([])], [0, 1])["ok"])
        self.assertFalse(self.verdict(sources, [self.claim([0, 1], "A는 10/24, B는 이번 주", "major")], [0, 1])["ok"])
        self.assertFalse(self.verdict(sources, [self.claim([0, 1], "한 매장을 전체로 확대", "major")], [0, 1])["ok"])

    def test_minor_notation_differences_do_not_block(self):
        sources = [self.news_a, self.news_b]
        claims = [self.claim([0, 1], "e스포츠/이스포츠 표기 차이", "minor")]
        self.assertTrue(self.verdict(sources, claims, [0, 1])["ok"])

    def test_out_of_range_source_numbers_are_ignored(self):
        self.assertFalse(self.verdict([self.news_a, self.news_b], [self.claim([0, 7])], [0, 7])["ok"])

    def test_official_notice_without_text_still_needs_news_coverage(self):
        notice = g.make_source("포켓몬코리아", "https://pokemoncard.co.kr/_news?id=1", "official", "(공식 공지) 제목")
        candidate, reason = g.write_verified_post("제목", [notice], [], self.now, "scheduled")
        self.assertIsNone(candidate)
        self.assertIn("다른 출처", reason)

    def test_single_non_official_source_is_rejected_before_writing(self):
        candidate, reason = g.write_verified_post("제목", [self.news_a], [], self.now, "scheduled")
        self.assertIsNone(candidate)
        self.assertIn("다른 출처", reason)


class BuyGuideTest(unittest.TestCase):
    today = date(2026, 10, 15)

    def items(self):
        def item(kind, source):
            return {"kind": kind, "source": source, "title": "", "url": "", "summary": "", "image": "",
                    "published": None, "dated_by_day": False}
        return [item("news", "비즈워치"), item("news", "세계일보"), item("threads", "Threads @gs25_store"),
                item("official", "포켓몬코리아(카드 게임)"), item("blog", "Pokemon Box House")]

    def problem(self, sources, first_party=None, history=()):
        opportunity = {"key": "GS25 30th CELEBRATION 확장팩 순차 입고", "sources": sources, "first_party": first_party}
        return g.buy_opportunity_problem(opportunity, self.items(), list(history), self.today)

    def test_two_outlets_official_or_seller_post_pass(self):
        self.assertEqual(self.problem([0, 1]), "")
        self.assertEqual(self.problem([3]), "")
        self.assertEqual(self.problem([2], first_party=2), "")

    def test_single_outlet_or_blog_or_unclaimed_social_fails(self):
        self.assertTrue(self.problem([0]))
        self.assertTrue(self.problem([0, 4, 4]) and self.problem([2]))

    def test_same_opportunity_within_three_days_fails(self):
        past = {"posted_at": "2026-10-14T12:00:00+09:00", "buy_keys": ["GS25 30th CELEBRATION 확장팩 순차 입고"]}
        self.assertIn("이미 올린", self.problem([0, 1], history=[past]))
        old = {**past, "posted_at": "2026-10-10T12:00:00+09:00"}
        self.assertEqual(self.problem([0, 1], history=[old]), "")


if __name__ == "__main__":
    unittest.main()
