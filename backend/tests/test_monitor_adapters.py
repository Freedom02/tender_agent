from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.tender_monitor.adapters import (
    GgzyAdapter,
    MonitorAdapterError,
    enrich_detail,
    extract_deadline,
    extract_registration_period,
    normalize_notice_text,
)
from backend.app.tender_monitor.custom_site import _parse_html


class MonitorAdapterTests(unittest.TestCase):
    def test_flattened_notice_restores_section_and_field_boundaries(self) -> None:
        flattened = (
            "某市平台采购公告一、项目基本情况1、项目编号：CS-2026-001 "
            "2、项目名称：城市运行平台项目二、采购需求：平台建设、数据接入及培训"
            "三、申请人的资格要求1、投标人须具有有效的营业执照。"
            "四、获取招标文件时间：2026年9月1日至2026年9月8日"
            "五、投标截止时间：2026年9月20日9时30分"
        )

        normalized = normalize_notice_text(flattened)

        self.assertIn("一、项目基本情况\n1、项目编号", normalized)
        self.assertIn("\n2、项目名称", normalized)
        self.assertIn("\n二、采购需求", normalized)
        self.assertIn("\n三、申请人的资格要求\n1、", normalized)
        self.assertIn("\n四、获取招标文件时间", normalized)
        self.assertIn("\n五、投标截止时间", normalized)

    def test_custom_html_content_preserves_paragraph_and_table_boundaries(self) -> None:
        html = """
        <article class="notice">
          <h1 class="title">平台公开招标公告</h1>
          <div class="body">
            <p>一、项目基本情况</p>
            <table><tr><td>项目编号</td><td>CS-2026-001</td></tr></table>
            <p>二、采购需求</p>
            <p>平台建设、数据接入及培训。</p>
          </div>
          <a class="link" href="/notice/1">查看</a>
        </article>
        """
        site = {
            "id": "custom",
            "name": "自定义网站",
            "url": "https://example.test",
            "selectors": {
                "item": "article.notice",
                "title": "h1.title",
                "content": "div.body",
                "link": "a.link",
            },
        }

        items = _parse_html(site, html)

        self.assertEqual(len(items), 1)
        self.assertIn("一、项目基本情况", items[0]["analysisText"])
        self.assertIn("项目编号", items[0]["analysisText"])
        self.assertIn("\n二、采购需求", items[0]["analysisText"])
        self.assertIn("\n平台建设、数据接入及培训。", items[0]["analysisText"])

    def test_ggzy_uses_current_official_search_host(self) -> None:
        site = {
            "id": "public-resource",
            "name": "全国公共资源交易平台",
            "url": "https://www.ggzy.gov.cn",
        }
        monitoring = {
            "includeKeywords": ["数字孪生"],
            "serviceType": "",
            "maxKeywordsPerRun": 1,
            "maxPagesPerKeyword": 1,
            "fetchDetails": False,
            "lookbackDays": 7,
        }
        requests: list[tuple[str, dict[str, object]]] = []

        def fetch_json(url: str, form: dict[str, object], timeout: int = 15, headers=None) -> dict:
            requests.append((url, form))
            return {
                "code": 200,
                "data": {
                    "records": [
                        {
                            "title": "数字孪生平台采购公告",
                            "url": "/information/deal/html/a/110000/0201/20260912/demo.html",
                            "publishTime": "2026-09-12",
                            "provinceText": "北京市",
                            "transactionSourcesPlatformText": "北京市公共资源交易平台",
                            "businessTypeText": "政府采购",
                            "informationTypeText": "采购/资审公告",
                        },
                        {
                            "title": "交易公开",
                            "url": "/deal/dealList.html",
                            "publishTime": "",
                        },
                    ]
                },
            }

        with patch("backend.app.tender_monitor.adapters.fetch_form_json", side_effect=fetch_json):
            items = GgzyAdapter().fetch(site, monitoring)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            requests[0][0],
            "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList",
        )
        self.assertEqual(requests[0][1]["FINDTXT"], "数字孪生")
        self.assertEqual(requests[0][1]["PAGENUMBER"], 1)
        self.assertIn("数字孪生平台采购公告", items[0]["title"])
        self.assertEqual(items[0]["publishedDate"], "2026-09-12")
        self.assertIn("北京市", items[0]["analysisText"])
        self.assertNotIn("deal.ggzy.gov.cn", items[0]["detailUrl"])

    def test_ggzy_raises_on_official_api_error(self) -> None:
        site = {"id": "public-resource", "name": "公共资源", "url": "https://www.ggzy.gov.cn"}
        monitoring = {
            "includeKeywords": ["数字孪生"],
            "maxKeywordsPerRun": 1,
            "maxPagesPerKeyword": 1,
            "fetchDetails": False,
            "lookbackDays": 7,
        }
        with patch(
            "backend.app.tender_monitor.adapters.fetch_form_json",
            return_value={"code": 829, "message": "需要验证码"},
        ):
            with self.assertRaisesRegex(MonitorAdapterError, "需要验证码"):
                GgzyAdapter().fetch(site, monitoring)

    def test_ggzy_enriches_summary_page_from_body_page_and_extracts_times(self) -> None:
        summary_url = "https://www.ggzy.gov.cn/information/deal/html/a/demo.html"
        body_url = "https://www.ggzy.gov.cn/information/deal/html/b/demo.html"
        summary_html = f"""
        <html><body>
          <a href="{body_url.replace('https://www.ggzy.gov.cn', '')}">公告内容文档</a>
        </body></html>
        """
        body_html = """
        <html><body>
          <h1>数字孪生平台公开招标公告</h1>
          <p>三、获取招标文件</p>
          <p>1.时间：2026年09月08日至2026年09月14日</p>
          <p>四、投标截止时间及地点</p>
          <p>1.时间：2026年09月28日10时00分（北京时间）</p>
        </body></html>
        """
        responses = iter([(summary_html, "text/html"), (body_html, "text/html")])
        item = {
            "title": "数字孪生平台公开招标公告",
            "sourceUrl": summary_url,
            "detailUrl": summary_url,
            "analysisText": "摘要",
        }

        with patch(
            "backend.app.tender_monitor.adapters.fetch_url",
            side_effect=lambda url, timeout=15: next(responses),
        ):
            result = enrich_detail(item)

        self.assertEqual(result["contentUrl"], body_url)
        self.assertIn("2026-09-08", result["registrationPeriod"])
        self.assertIn("2026-09-14", result["registrationPeriod"])
        self.assertEqual(result["bidDeadline"], "2026-09-28 10:00")

    def test_time_extractors_handle_spaced_digits_and_common_section_labels(self) -> None:
        text = (
            "6.招标文件的获取 6.1 招标文件获取时间为：202 6 年 9 月 9 日 1 7 时 3 0 分至 "
            "202 6 年 9 月 2 9 日 2 3 时 5 9 分。"
            "7.投标文件的递交 7.1 投标文件递交截止时间为：202 6 年 9 月 3 0 日 9 时 3 0 分。"
        )

        self.assertEqual(
            extract_registration_period(text),
            "2026-09-09 17:30 至 2026-09-29 23:59",
        )
        self.assertEqual(extract_deadline(text), "2026-09-30 09:30")


if __name__ == "__main__":
    unittest.main()
