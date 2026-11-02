import { TimeValue, DayAxis, AllocationChart, SleepDetail, compactMinutes } from '../lib/dashboard-ui';
import {
  HistoryDay,
  SleepSummary,
  TimeMirrorSnapshot,
  formatDateLabel,
  formatTime,
  isTimeMirrorSnapshot,
} from '../lib/snapshot';

const SNAPSHOT_URL = 'http://127.0.0.1:8765/snapshot.json';

export default async function Home() {
  const snapshot = await loadSnapshot();
  if (!snapshot) return <UnavailableDashboard />;

  return (
    <main className="kindle-page">
      <meta httpEquiv="refresh" content="300" />
      <header className="page-header">
        <div className="brand">
          <p className="eyebrow">{formatDateLabel(snapshot.statisticalDate)}</p>
          <h1>我的一天</h1>
        </div>
        <div className="remaining-summary">
          <span>今天还能安排</span>
          <TimeValue minutes={snapshot.remaining.totalMinutes} />
          <p>
            {snapshot.remaining.countdownMinutes !== undefined
              ? `距 ${formatTime(snapshot.remaining.untilAt ?? '') || '00:00'} 还有 ${compactMinutes(snapshot.remaining.countdownMinutes)}`
              : '今晚规划至 00:00'}
            {snapshot.allocation ? ` · 尚待预留 ${compactMinutes(snapshot.allocation.futureReservedMinutes)}` : ''}
          </p>
        </div>
        <div className="clock" aria-label={`快照生成时间 ${formatTime(snapshot.generatedAt)}`}>
          <strong>{formatTime(snapshot.generatedAt)}</strong>
          {snapshot.allocation ? <>
            <span>全天固定安排 {compactMinutes(snapshot.allocation.fixedMinutes)}</span>
            <span>其余时间 {compactMinutes(snapshot.allocation.otherPlanMinutes)}</span>
          </> : <span>本机快照</span>}
        </div>
      </header>

      <section className="day-section" aria-labelledby="day-title">
        <div className="section-heading"><h2 id="day-title">今天怎么过的</h2><span>00:00 — 24:00</span></div>
        <DayAxis snapshot={snapshot} />
      </section>

      <div className="charts-grid">
        <section className="chart-panel allocation-panel" aria-labelledby="allocation-title">
          <div className="section-heading"><h2 id="allocation-title">一天的分配</h2><span>占全天 24h</span></div>
          {snapshot.allocation
            ? <AllocationChart allocation={snapshot.allocation} elapsedMinutes={snapshot.coverage.elapsedMinutes} />
            : <div className="chart-unavailable">分配数据暂不可用</div>}
        </section>
        <TrendChart days={snapshot.history?.days ?? []} sleep={snapshot.sleepSummary} />
      </div>

      <footer className="page-footer">
        <span>{sleepSourceNote(snapshot)}</span>
        <span>{sourceSummary(snapshot)}</span>
        <span>最后更新 {formatTime(snapshot.generatedAt)}</span>
      </footer>
    </main>
  );
}

function sleepSourceNote(snapshot: TimeMirrorSnapshot): string {
  const health = snapshot.sources.health;
  let sleep = '睡眠待记录';
  if (!health || health.status === 'not_connected') sleep = '睡眠未接入';
  else if (health.status === 'error' || health.status === 'unavailable') sleep = '睡眠数据暂不可用';
  else if (health.errorCode === 'sleep_session_span') sleep = '睡眠按寝起时段，可能含清醒';
  else if (health.errorCode !== 'no_sleep_record' && health.latestEventAt) sleep = '睡眠来自手环';
  return `${sleep} · 日常及上课估算`;
}

type TrendKey = 'phonePercent' | 'readingPercent';
const TREND_SERIES: Array<{ key: TrendKey; label: string; className: string }> = [
  { key: 'phonePercent', label: '手机', className: 'phone' },
  { key: 'readingPercent', label: '阅读', className: 'reading' },
];
const TREND_BOUNDS = { left: 34, right: 311, top: 15, bottom: 148 };

function TrendChart({ days, sleep }: { days: HistoryDay[]; sleep?: SleepSummary | null }) {
  const values = days.flatMap((day) => [day.phonePercent, day.readingPercent]).filter((value): value is number => value !== null);
  const hasPoints = values.length > 0;
  const hasIncompletePoints = days.some((day) =>
    (day.phonePercent !== null && !day.phoneComplete) || (day.readingPercent !== null && !day.readingComplete));
  const step = !hasPoints ? 10 : Math.max(...values, 0) > 50 ? 25 : Math.max(...values, 0) > 20 ? 10 : 5;
  const ceiling = hasPoints ? Math.max(step, Math.ceil(Math.max(...values) / step) * step) : 30;
  const ticks = Array.from({ length: Math.round(ceiling / step) + 1 }, (_, index) => index * step);
  const x = (index: number) => trendX(index, days.length);
  const y = (percent: number) => TREND_BOUNDS.bottom - percent / ceiling * (TREND_BOUNDS.bottom - TREND_BOUNDS.top);

  return (
    <section className="chart-panel trend-panel" aria-labelledby="trend-title">
      <div className="section-heading"><h2 id="trend-title">手机与阅读</h2><span>{historyRange(days)}</span></div>
      <div className="trend-key"><span><i className="trend-key-phone" />手机</span><span><i className="trend-key-reading" />阅读</span><span>占全天 %</span></div>
      <svg className="trend-chart" viewBox="0 0 359 176" role="img" aria-label="最近七个已结束统计日，手机与阅读占全天比例">
        <desc>{historyDescription(days)}</desc>
        {ticks.map((value) => <g key={value}>
          <line x1={TREND_BOUNDS.left} x2={TREND_BOUNDS.right} y1={y(value)} y2={y(value)} className="trend-grid" />
          <text x={TREND_BOUNDS.left - 7} y={y(value) + 4} textAnchor="end" className="trend-axis-label">{value}</text>
        </g>)}
        {days.map((day, index) => <text key={day.date} x={x(index)} y="169" textAnchor="middle"
          className={`trend-axis-label ${index % 2 ? 'trend-date-secondary' : ''}`}>{Number(day.date.slice(8, 10))}</text>)}
        {TREND_SERIES.map((series) => <TrendSeries key={series.key} days={days} series={series} x={x} y={y} />)}
        {!hasPoints && <text x="173" y="85" textAnchor="middle" className="trend-empty">暂无可显示记录</text>}
      </svg>
      <p className="trend-detail">{hasPoints ? `${latestHistorySummary(days)}${hasIncompletePoints ? ' · 部分记录，可能不完整' : ''}` : '没有可用记录，缺失不按零计算'}</p>
      <SleepDetail sleep={sleep} />
    </section>
  );
}

function TrendSeries({ days, series, x, y }: {
  days: HistoryDay[];
  series: (typeof TREND_SERIES)[number];
  x: (index: number) => number;
  y: (percent: number) => number;
}) {
  const points = days.flatMap((day, index) => day[series.key] === null ? [] : [{ day, index, value: day[series.key] as number }]);
  const latest = points.at(-1);
  return <g className={`trend-series trend-series--${series.className}`}>
    <path d={trendPath(days, series.key, x, y)} className="trend-line" />
    {points.map(({ day, index, value }) => series.className === 'phone'
      ? <circle key={day.date} cx={x(index)} cy={y(value)} r="2.8"><title>{`${day.date} · ${series.label} ${value.toFixed(1)}%${day.phoneComplete ? '' : ' · 记录可能不完整'}`}</title></circle>
      : <rect key={day.date} x={x(index) - 2.8} y={y(value) - 2.8} width="5.6" height="5.6"><title>{`${day.date} · ${series.label} ${value.toFixed(1)}%${day.readingComplete ? '' : ' · 记录可能不完整'}`}</title></rect>)}
    {latest && <text x={x(latest.index) + 7} y={endpointY(days, series.key, latest.index, y)} className="trend-end-label">{latest.value.toFixed(1)}%</text>}
  </g>;
}

function trendPath(days: HistoryDay[], key: TrendKey, x: (index: number) => number, y: (value: number) => number): string {
  let path = '';
  let connected = false;
  days.forEach((day, index) => {
    const value = day[key];
    if (value === null) { connected = false; return; }
    path += `${connected ? 'L' : 'M'}${x(index)},${y(value)} `;
    connected = true;
  });
  return path;
}

function endpointY(days: HistoryDay[], key: TrendKey, index: number, y: (value: number) => number): number {
  const day = days[index];
  const value = day[key] as number;
  const other = day[key === 'phonePercent' ? 'readingPercent' : 'phonePercent'];
  const offset = other !== null && Math.abs(y(value) - y(other)) < 16 ? (key === 'phonePercent' ? -7 : 11) : 4;
  return Math.max(13, Math.min(155, y(value) + offset));
}

function trendX(index: number, length: number): number {
  return length <= 1 ? (TREND_BOUNDS.left + TREND_BOUNDS.right) / 2
    : TREND_BOUNDS.left + index / (length - 1) * (TREND_BOUNDS.right - TREND_BOUNDS.left);
}

function historyDescription(days: HistoryDay[]): string {
  return days.map((day) => `${day.date}，手机${day.phonePercent === null ? '缺记录' : `${day.phonePercent.toFixed(1)}%${day.phoneComplete ? '' : '（可能不完整）'}`}，阅读${day.readingPercent === null ? '缺记录' : `${day.readingPercent.toFixed(1)}%${day.readingComplete ? '' : '（可能不完整）'}`}`).join('；') || '历史记录尚未就绪';
}

function latestHistorySummary(days: HistoryDay[]): string {
  return TREND_SERIES.map((series) => {
    const day = [...days].reverse().find((item) => item[series.key] !== null);
    const minutes = day?.[series.key === 'phonePercent' ? 'phoneMinutes' : 'readingMinutes'];
    return day ? `${series.label} ${shortDate(day.date)} ${minutes === null || minutes === undefined ? '' : compactMinutes(minutes)} / ${(day[series.key] as number).toFixed(1)}%` : `${series.label}缺记录`;
  }).join(' · ');
}

function historyRange(days: HistoryDay[]): string {
  return days.length > 0 ? `${shortDate(days[0].date)}—${shortDate(days[days.length - 1].date)}` : '近 7 个完整日';
}

function historyStatus(days: HistoryDay[]): string {
  if (days.length === 0) return '历史记录尚未就绪';
  const missing = days.filter((day) => day.phonePercent === null || day.readingPercent === null).length;
  return missing ? `近 ${days.length} 日有 ${missing} 日记录不完整` : '近 7 个完整统计日';
}

function sourceSummary(snapshot: TimeMirrorSnapshot): string {
  const phone = snapshot.sources.phone;
  const notes: string[] = [];
  if (phone?.errorCode === 'awaiting_today_sync') {
    const at = phone.collectedAt;
    notes.push(`手机待同步${at ? ` · ${shortDate(at)} ${formatTime(at)}` : ''}`);
  } else if (phone && !['ok', 'cached'].includes(phone.status)) {
    notes.push('手机暂未接入');
  } else if (phone?.status === 'cached') {
    notes.push('手机使用缓存');
  } else if (!phone?.latestEventAt) {
    notes.push('手机今日暂无记录');
  }
  if (snapshot.sources.calendar?.status === 'cached') notes.push('日历缓存');
  else if (snapshot.sources.calendar?.status !== 'ok') notes.push('日历暂不可用');
  return notes.join(' · ') || historyStatus(snapshot.history?.days ?? []);
}

function shortDate(value: string): string {
  return `${Number(value.slice(5, 7))}/${Number(value.slice(8, 10))}`;
}

async function loadSnapshot(): Promise<TimeMirrorSnapshot | null> {
  try {
    const response = await fetch(SNAPSHOT_URL, { cache: 'no-store' });
    if (!response.ok) return null;
    const value: unknown = await response.json();
    return isTimeMirrorSnapshot(value) ? value : null;
  } catch {
    return null;
  }
}

function UnavailableDashboard() {
  return <main className="kindle-page kindle-page--empty">
    <meta httpEquiv="refresh" content="300" />
    <header className="page-header"><div><p className="eyebrow">TIME MIRROR</p><h1>自动牛马机</h1></div></header>
    <section className="empty-state"><strong>本机快照暂时不可用</strong><p>请运行 start-dashboard.cmd 后刷新。</p></section>
  </main>;
}

