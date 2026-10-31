"""外部数据源的只读采集边界。"""

from .koreader import BookSummary, KoreaderDatabase, ReadingEvent, copy_snapshot

__all__ = ["BookSummary", "KoreaderDatabase", "ReadingEvent", "copy_snapshot"]

