# encoding=utf-8
import unittest
from unittest.mock import AsyncMock, MagicMock

from modules.course_crawler import (
    CourseCrawler,
    format_seconds,
    find_lessons_in_json,
    parse_duration_to_seconds,
)
from modules.configs import Config
from modules.lesson_navigation import WISDOM_CATALOG


class CourseCrawlerTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_duration_numeric(self):
        self.assertEqual(parse_duration_to_seconds(120), 120.0)
        self.assertEqual(parse_duration_to_seconds("300"), 300.0)
        self.assertEqual(parse_duration_to_seconds("45.5"), 45.5)

    def test_parse_duration_mmss_and_hhmmss(self):
        self.assertEqual(parse_duration_to_seconds("12:34"), 12 * 60 + 34)
        self.assertEqual(parse_duration_to_seconds("01:10:20"), 1 * 3600 + 10 * 60 + 20)
        self.assertEqual(parse_duration_to_seconds("00:05:09"), 5 * 60 + 9)

    def test_parse_duration_chinese(self):
        self.assertEqual(parse_duration_to_seconds("15分20秒"), 15 * 60 + 20)
        self.assertEqual(parse_duration_to_seconds("1小时5分30秒"), 3600 + 5 * 60 + 30)
        self.assertEqual(parse_duration_to_seconds("45秒"), 45.0)

    def test_parse_duration_invalid(self):
        self.assertIsNone(parse_duration_to_seconds(None))
        self.assertIsNone(parse_duration_to_seconds(""))
        self.assertIsNone(parse_duration_to_seconds("invalid"))
        self.assertIsNone(parse_duration_to_seconds(-10))

    def test_format_seconds(self):
        self.assertEqual(format_seconds(45), "45秒")
        self.assertEqual(format_seconds(125), "2分05秒")
        self.assertEqual(format_seconds(3665), "1小时1分05秒")

    def test_find_lessons_in_json(self):
        sample_json = {
            "code": 200,
            "data": {
                "courseName": "测试课程",
                "chapters": [
                    {
                        "name": "第一章",
                        "lessons": [
                            {"lessonName": "1.1 绪论", "videoSec": 600},
                            {"lessonName": "1.2 背景", "timeLength": "15:20"},
                        ]
                    },
                    {
                        "name": "第二章",
                        "lessons": [
                            {"title": "2.1 算法基础", "duration": 1800},
                        ]
                    }
                ]
            }
        }
        lessons = find_lessons_in_json(sample_json)
        self.assertEqual(len(lessons), 3)
        self.assertEqual(lessons[0]["title"], "1.1 绪论")
        self.assertEqual(lessons[0]["seconds"], 600.0)
        self.assertEqual(lessons[1]["title"], "1.2 背景")
        self.assertEqual(lessons[1]["seconds"], 15 * 60 + 20)
        self.assertEqual(lessons[2]["title"], "2.1 算法基础")
        self.assertEqual(lessons[2]["seconds"], 1800.0)

    async def test_calculate_and_apply_max_time(self):
        crawler = CourseCrawler()
        crawler.crawl_course_lessons = AsyncMock(return_value=[
            {"title": "小节1", "seconds": 600.0},    # 10 分钟
            {"title": "最长小节", "seconds": 1520.0}, # 25.33 分钟
            {"title": "小节3", "seconds": 900.0},    # 15 分钟
        ])

        config = MagicMock()
        config.limitSpeed = 1.0
        config.limitMaxTime = 30.0
        logger = MagicMock()

        # 1.0 倍速: 25.33 / 1.0 = 25.33 -> ceil=26 + buffer 1 = 27.0
        new_limit = await crawler.calculate_and_apply_max_time(
            page=None, catalog=WISDOM_CATALOG, config=config, logger=logger, buffer_minutes=1.0
        )
        self.assertEqual(new_limit, 27.0)
        self.assertEqual(config.limitMaxTime, 27.0)

        # 1.5 倍速: 25.33 / 1.5 = 16.89 -> ceil=17 + buffer 1 = 18.0
        config.limitSpeed = 1.5
        new_limit_15 = await crawler.calculate_and_apply_max_time(
            page=None, catalog=WISDOM_CATALOG, config=config, logger=logger, buffer_minutes=1.0
        )
        self.assertEqual(new_limit_15, 18.0)
        self.assertEqual(config.limitMaxTime, 18.0)

    def test_config_limit_max_time_override(self):
        cfg = Config.__new__(Config)
        cfg._config = MagicMock()
        cfg._safe_get_float = MagicMock(return_value=30.0)
        cfg._read_config = MagicMock()

        # 默认从文件读取
        self.assertEqual(cfg.limitMaxTime, 30.0)

        # 动态覆盖
        cfg.limitMaxTime = 42.5
        self.assertEqual(cfg.limitMaxTime, 42.5)

        # 重置覆盖
        cfg.reset_limit_max_time()
        self.assertEqual(cfg.limitMaxTime, 30.0)


if __name__ == "__main__":
    unittest.main()
