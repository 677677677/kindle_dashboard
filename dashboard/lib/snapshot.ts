export type SourceStatus = 'ok' | 'cached' | 'unavailable' | 'error' | 'not_connected';
export type TimelineState = 'observed' | 'inferred' | 'planned' | 'unknown' | 'open';

export type SourceState = {
  status: SourceStatus;
  collectedAt?: string | null;
  latestEventAt?: string | null;
  errorCode?: string | null;
  sleepEstimated?: boolean;
};

export type DeviceSnapshot = {
  id: 'kindle' | 'computer' | 'phone';
  name: string;
  status: SourceStatus;
  totalMinutes: number | null;
  books?: Array<{
    title: string;
    authors: string;
    minutes: number;
    lastReadAt: string;
  }>;
  apps?: Array<{
    name: string;
    minutes: number;
    sessions?: number;
    longestSessionMinutes?: number;
  }>;
  longestSegment?: {
    app: string;
    startAt: string;
    endAt: string;
    minutes: number;
  } | null;
  frequentApps?: Array<{ name: string; sessions: number; minutes: number }>;
  recent?: { title: string; authors: string; lastReadAt: string } | null;
};

export type AllocationCategory = {
  id: 'sleep' | 'study' | 'entertainment' | 'reading' | 'daily' | 'other' | 'future';
  label: string;
  seconds: number;
  minutes: number;
  percent: number;
  estimatedSeconds: number;
};

export type AllocationSnapshot = {
  version: string;
  totalSeconds: number;
  fixedMinutes: number;
  otherPlanMinutes: number;
  futureReservedMinutes: number;
  observedSeconds: number;
  unknownSeconds: number;
  categories: AllocationCategory[];
  entertainmentBreakdown?: Array<{ name: string; device: 'phone' | 'computer' | 'unknown'; seconds: number; minutes: number }>;
};

export type SleepSummary = {
  minutes: number;
  estimated: boolean;
  startAt?: string;
  endAt?: string;
  intervals: Array<{ startAt: string; endAt: string; estimated: boolean }>;
};

export type HistoryDay = {
  date: string;
  phoneMinutes: number | null;
  readingMinutes: number | null;
  phonePercent: number | null;
  readingPercent: number | null;
  status: 'complete' | 'partial' | 'missing';
  phoneComplete?: boolean;
  readingComplete?: boolean;
};

export type HistorySnapshot = { version: string; days: HistoryDay[] };

export type TimeMirrorSnapshot = {
  schemaVersion: 'snapshot-v1';
  timezone: string;
  statisticalDate: string;
  dayStart: string;
  dayEnd: string;
  generatedAt: string;
  overallStatus: 'ok' | 'partial' | 'error';
  sources: Record<string, SourceState>;
  current: { label: string; startedAt: string; detail?: string } | null;
  next: { label: string; startAt: string } | null;
  remaining: {
    status: 'exact' | 'estimate';
    totalMinutes: number;
    scheduledFreeMinutes: number;
    unplacedReserveMinutes: number;
    reserveBreakdown?: Array<{ label: string; minutes: number }>;
    largestScheduledGapMinutes: number;
    largestBlockMinutes: number | null;
    note: string;
    untilAt?: string;
    countdownMinutes?: number;
  };
  coverage: { elapsedMinutes: number; classifiedMinutes: number; accountedPercent: number };
  timeline: Array<{
    id: string;
    startAt: string;
    endAt: string;
    minutes: number;
    label: string;
    state: TimelineState;
  }>;
  dayAxis?: {
    minimumPhoneMinutes?: number;
    reserves: Array<{ startAt: string; endAt: string }>;
    phone?: Array<{ app: 'rednote' | 'bilibili'; startAt: string; endAt: string }>;
    appSegments?: Array<{ device: 'phone' | 'computer' | 'combined'; app: string; startAt: string; endAt: string; minutes: number }>;
  };
  sleepSummary?: SleepSummary | null;
  allocation?: AllocationSnapshot;
  history?: HistorySnapshot;
  highlights: Array<{
    startAt: string;
    endAt: string;
    label: string;
    detail: string;
    source: string;
  }>;
  calendarEvents: Array<{
    id: string;
    subject: string;
    startAt: string;
    endAt: string;
    showAs: string;
    isAllDay: boolean;
    blocksTime: boolean;
  }>;
  devices: DeviceSnapshot[];
  prompt: { kind: string; text: string };
};

export function isTimeMirrorSnapshot(value: unknown): value is TimeMirrorSnapshot {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<TimeMirrorSnapshot>;
  return (
    candidate.schemaVersion === 'snapshot-v1' &&
    typeof candidate.generatedAt === 'string' &&
    typeof candidate.statisticalDate === 'string' &&
    Array.isArray(candidate.timeline) &&
    Array.isArray(candidate.devices) &&
    Boolean(candidate.remaining) &&
    Boolean(candidate.coverage) &&
    Boolean(candidate.sources)
  );
}

export function formatMinutes(totalMinutes: number | null): string {
  if (totalMinutes === null) return '未接入';
  const safeMinutes = Math.max(0, Math.round(totalMinutes));
  const hours = Math.floor(safeMinutes / 60);
  const minutes = safeMinutes % 60;

  if (hours === 0) return `${minutes} 分钟`;
  if (minutes === 0) return `${hours} 小时`;
  return `${hours} 小时 ${minutes} 分`;
}

export function formatTime(value: string): string {
  return value.slice(11, 16);
}

export function formatDateLabel(value: string): string {
  const [year, month, day] = value.split('-').map(Number);
  const weekday = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][
    new Date(Date.UTC(year, month - 1, day)).getUTCDay()
  ];
  return `${month} 月 ${day} 日 · ${weekday}`;
}

export function formatShortDateTime(value: string): string {
  const [, month, rest] = value.match(/^\d{4}-(\d{2})-(\d{2}T\d{2}:\d{2})/) ?? [];
  if (!month || !rest) return value;
  const [day, time] = rest.split('T');
  return `${Number(month)}月${Number(day)}日 ${time}`;
}

export function sourceStatusLabel(source: SourceState | undefined): string {
  if (!source) return '状态未知';
  if (source.status === 'ok') return '已同步';
  if (source.status === 'cached') {
    return source.collectedAt ? `缓存 · ${formatShortDateTime(source.collectedAt)}` : '缓存';
  }
  if (source.status === 'unavailable') return '暂无数据';
  if (source.status === 'error') return '数据异常';
  return '未接入';
}

