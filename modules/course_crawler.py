# encoding=utf-8
import asyncio
import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple
from playwright.async_api import Page, Response

from modules.lesson_navigation import CatalogSelectors
from modules.fusion_adapter import FusionAdapter


def parse_duration_to_seconds(value: Any) -> Optional[float]:
    """将多种格式的时长转换为秒数。
    
    支持:
    - 数字或纯数字字符串: 125, "125", "125.5"
    - "MM:SS": "12:34" -> 754.0
    - "HH:MM:SS": "01:10:20" -> 4220.0
    - 中文时间: "12分34秒", "1小时10分20秒"
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value > 0 else None

    text = str(value).strip()
    if not text:
        return None

    # 纯数字
    try:
        num = float(text)
        if math.isfinite(num) and num > 0:
            return num
    except ValueError:
        pass

    # 中文格式: (X小时)?(Y分)?(Z秒)?
    cn_match = re.search(r'(?:(\d+)\s*(?:小时|时|h))?\s*(?:(\d+)\s*(?:分钟|分|m))?\s*(?:(\d+)\s*(?:秒钟|秒|s))?', text)
    if cn_match and any(cn_match.groups()):
        hours = int(cn_match.group(1) or 0)
        minutes = int(cn_match.group(2) or 0)
        seconds = int(cn_match.group(3) or 0)
        total = hours * 3600 + minutes * 60 + seconds
        if total > 0:
            return float(total)

    # 常见标准格式: HH:MM:SS 或 MM:SS
    # 例子: 05:32 或 01:20:30
    time_match = re.search(r'(?:(?:(\d{1,2}):)?(\d{1,2}):(\d{2}))', text)
    if time_match:
        hours = int(time_match.group(1)) if time_match.group(1) is not None else 0
        minutes = int(time_match.group(2))
        seconds = int(time_match.group(3))
        total = hours * 3600 + minutes * 60 + seconds
        if total > 0:
            return float(total)

    return None


def format_seconds(seconds: float) -> str:
    """将秒数格式化为人类友好的可读字符串 (如 '15分20秒' 或 '1小时10分05秒')。"""
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "0秒"
    total_sec = int(round(seconds))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}分")
    parts.append(f"{secs:02d}秒" if (hours > 0 or minutes > 0) else f"{secs}秒")
    return "".join(parts)


def find_lessons_in_json(data: Any) -> List[Dict[str, Any]]:
    """递归提取 JSON 数据中包含小节视频时长和标题的对象。"""
    lessons = []

    def _walk(node: Any):
        if isinstance(node, dict):
            # 常见视频课时字段: videoSec, duration, videoDuration, timeLength
            duration_val = (
                node.get("videoSec")
                or node.get("videoDuration")
                or node.get("duration")
                or node.get("timeLength")
                or node.get("videoTime")
            )
            title = (
                node.get("lessonName")
                or node.get("name")
                or node.get("title")
                or node.get("sectionName")
            )
            parsed_sec = parse_duration_to_seconds(duration_val)
            if parsed_sec and parsed_sec > 0:
                lessons.append({
                    "title": str(title or "未命名小节").strip(),
                    "seconds": parsed_sec,
                    "source": "api"
                })
            for val in node.values():
                _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    return lessons


class CourseCrawler:
    """智慧树课程爬虫: 负责爬取并解析当前课程全部小节时长, 计算最长小节."""

    def __init__(self):
        self._intercepted_lessons: List[Dict[str, Any]] = []
        self._listener_attached = False

    def attach_response_listener(self, page: Page) -> None:
        """注册网络响应监听, 捕获智慧树课程目录接口数据。"""
        if self._listener_attached:
            return

        async def _handle_response(response: Response):
            try:
                url = response.url
                # 过滤相关的课时接口
                if any(k in url for k in ("learning", "lesson", "catalog", "chapter", "video", "loadVideoList", "preViewLessons")):
                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type:
                        data = await response.json()
                        extracted = find_lessons_in_json(data)
                        if extracted:
                            self._intercepted_lessons.extend(extracted)
            except Exception:
                pass

        page.on("response", _handle_response)
        self._listener_attached = True

    async def crawl_durations_from_dom(self, page: Page, catalog: CatalogSelectors) -> List[Dict[str, Any]]:
        """从页面 DOM 结构爬取所有小节的时长与标题。"""
        # 融合课如果折叠需先展开
        if catalog.name == "fusion":
            try:
                await FusionAdapter.expand_catalog(page)
                await page.wait_for_timeout(300)
            except Exception:
                pass

        script = f"""() => {{
            const items = document.querySelectorAll({json.dumps(catalog.item)});
            const results = [];
            for (let i = 0; i < items.length; i++) {{
                const item = items[i];
                // 查找标题
                let title = "";
                const titleEl = item.querySelector({json.dumps(catalog.title)});
                if (titleEl) {{
                    title = titleEl.getAttribute("title") || titleEl.innerText || "";
                }}
                if (!title) {{
                    title = item.innerText ? item.innerText.split('\\n')[0] : "";
                }}

                // 查找时长元素或直接提取全文本匹配
                let rawTime = "";
                const timeEl = item.querySelector(".time, .video-time, .duration, span.time, .time_icofinish + span, .catalogue_title_time");
                if (timeEl) {{
                    rawTime = timeEl.innerText || "";
                }}
                if (!rawTime) {{
                    // 从整个 item 的文本中正则匹配时间格式 (MM:SS 或 HH:MM:SS)
                    const text = item.innerText || "";
                    const match = text.match(/(?:\\d{{1,2}}:)?\\d{{1,2}}:\\d{{2}}/);
                    if (match) {{
                        rawTime = match[0];
                    }}
                }}
                results.push({{
                    title: title.trim(),
                    raw_time: rawTime.trim()
                }});
            }}
            return results;
        }}"""

        try:
            raw_items = await page.evaluate(script)
        except Exception:
            raw_items = []

        lessons = []
        for idx, item in enumerate(raw_items):
            sec = parse_duration_to_seconds(item.get("raw_time"))
            title = item.get("title") or f"第{idx+1}小节"
            if sec and sec > 0:
                lessons.append({
                    "title": title,
                    "seconds": sec,
                    "source": "dom"
                })
        return lessons

    async def crawl_course_lessons(self, page: Page, catalog: CatalogSelectors) -> List[Dict[str, Any]]:
        """汇总网络接口与 DOM 解析提取出的全部小节信息。"""
        dom_lessons = await self.crawl_durations_from_dom(page, catalog)
        if dom_lessons:
            return dom_lessons
        if self._intercepted_lessons:
            return self._intercepted_lessons
        return []

    async def calculate_and_apply_max_time(
        self,
        page: Page,
        catalog: CatalogSelectors,
        config: Any,
        logger: Any,
        buffer_minutes: float = 1.0,
    ) -> Optional[float]:
        """爬取所有小节时长，并把 config.limitMaxTime 设置为当前课程的最长小节时长。
        
        :param buffer_minutes: 在最长小节基础上增加的缓冲时间（分钟），防止网络波动在结尾处超时，默认 1.0 分钟。
        :return: 设置后的 limitMaxTime 分钟数；若未爬取到则返回当前已有的 limitMaxTime。
        """
        logger.info("正在爬取当前课程各小节视频时长...")
        lessons = await self.crawl_course_lessons(page, catalog)

        if not lessons:
            logger.warn("未能在当前页面爬取到小节时长,将保持原配置的 limitMaxTime 挂机时限.", shift=True)
            return getattr(config, "limitMaxTime", 0.0)

        # 找到最长的小节
        longest_lesson = max(lessons, key=lambda x: x["seconds"])
        max_sec = longest_lesson["seconds"]
        max_min_raw = max_sec / 60.0
        
        # 加上适度安全余量（向上取整 + buffer_minutes），确保视频能完整看完且不被截断
        calculated_limit = float(math.ceil(max_min_raw) + buffer_minutes)
        # 如果原始配置为 0（不限时），若用户未强制限制，可做友好提示，但在此我们按照用户要求赋值
        original_limit = config.limitMaxTime

        # 动态更新到 config
        config.limitMaxTime = calculated_limit

        logger.info(
            f"成功爬取到 {len(lessons)} 个小节! 最长小节: <<{longest_lesson['title']}>>, 时长: {format_seconds(max_sec)} ({max_min_raw:.1f}分钟)"
        )
        logger.info(
            f"已自动将当前课程挂机时限 limitMaxTime 调整为: {calculated_limit:.1f} 分钟 (包含 {buffer_minutes:.1f} 分钟缓冲余量, 原配置: {original_limit}分钟)"
        )
        logger.event(
            "爬虫小节统计",
            小节总数=len(lessons),
            最长课时=longest_lesson["title"],
            最长时长=format_seconds(max_sec),
            设定时限=f"{calculated_limit}min",
        )
        return calculated_limit
