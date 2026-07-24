# -*- coding: utf-8 -*-
"""Tests for the keyless Yahoo Finance market-news provider."""

import unittest
from unittest.mock import patch

from src.search_service import SearchService, YahooFinanceNewsProvider


class TestYahooFinanceNewsProvider(unittest.TestCase):
    @patch("yfinance.Search")
    def test_normalizes_legacy_yfinance_news_payload(self, mock_search):
        mock_search.return_value.news = [
            {
                "title": "Apple shares rise after services update",
                "publisher": "Reuters",
                "link": "https://finance.yahoo.com/news/apple-services-update",
                "providerPublishTime": 1784874600,
                "relatedTickers": ["AAPL"],
            }
        ]

        response = YahooFinanceNewsProvider().search("Apple AAPL", max_results=3, days=3)

        self.assertTrue(response.success)
        self.assertEqual(response.provider, "YahooFinance")
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].source, "Reuters")
        self.assertIn("AAPL", response.results[0].snippet)
        self.assertTrue(response.results[0].published_date.startswith("2026-"))

    @patch("yfinance.Search")
    def test_normalizes_current_nested_payload(self, mock_search):
        mock_search.return_value.news = [
            {
                "content": {
                    "title": "Tesla reports quarterly deliveries",
                    "summary": "Tesla delivery growth accelerated.",
                    "pubDate": "2026-07-24T08:00:00Z",
                    "provider": {"displayName": "MarketWire"},
                    "canonicalUrl": {"url": "https://example.com/tesla-deliveries"},
                }
            }
        ]

        response = YahooFinanceNewsProvider().search("Tesla TSLA", max_results=3, days=3)

        self.assertEqual(response.results[0].title, "Tesla reports quarterly deliveries")
        self.assertEqual(response.results[0].snippet, "Tesla delivery growth accelerated.")
        self.assertEqual(response.results[0].source, "MarketWire")

    def test_service_prefers_yahoo_for_foreign_stocks_only_when_enabled(self):
        service = SearchService(
            tavily_keys=["test-key"],
            searxng_public_instances_enabled=False,
            yahoo_finance_news_enabled=True,
        )

        us_providers = service._stock_news_providers("AAPL")
        cn_providers = service._stock_news_providers("600519")

        self.assertEqual(us_providers[0].name, "YahooFinance")
        self.assertNotIn("YahooFinance", [provider.name for provider in cn_providers])

    def test_service_keeps_yahoo_disabled_for_legacy_direct_construction(self):
        service = SearchService(searxng_public_instances_enabled=False)
        self.assertFalse(service.is_available)
        self.assertEqual(service._stock_news_providers("AAPL"), [])

    @patch.object(YahooFinanceNewsProvider, "search")
    def test_stock_news_uses_compact_symbol_query_for_yahoo(self, mock_search):
        from src.search_service import SearchResponse

        mock_search.return_value = SearchResponse(
            query="AAPL Apple",
            results=[],
            provider="YahooFinance",
            success=True,
        )
        service = SearchService(
            searxng_public_instances_enabled=False,
            yahoo_finance_news_enabled=True,
        )

        service.search_stock_news("AAPL", "Apple", max_results=3)

        self.assertEqual(mock_search.call_args.args[0], "AAPL Apple")


if __name__ == "__main__":
    unittest.main()
