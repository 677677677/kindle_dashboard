import {
  AllocationCategory,
  AllocationSnapshot,
  HistoryDay,
  SleepSummary,
  TimeMirrorSnapshot,
  formatDateLabel,
  formatMinutes,
  formatTime,
  isTimeMirrorSnapshot,
} from '../lib/snapshot';

const SNAPSHOT_URL = 'http://127.0.0.1:8765/snapshot.json';
const AXIS_TICKS = ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00', '18:00', '21:00', '24:00'];

export default async function Home() {
  const snapshot = await loadSnapshot();
  if (!snapshot) return <UnavailableDashboard />;

  return (
    <main className="kindle-page">
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

function TimeValue({ minutes }: { minutes: number }) {
  const rounded = Math.max(0, Math.round(minutes));
  const hours = Math.floor(rounded / 60);
  return <strong className="remaining-value" aria-label={`约 ${formatMinutes(rounded)}`}>
    {hours > 0 && <>{hours}<small>小时</small></>}
    {(rounded % 60 > 0 || hours === 0) && <>{rounded % 60}<small>分</small></>}
  </strong>;
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

function DayAxis({ snapshot }: { snapshot: TimeMirrorSnapshot }) {
  const position = (startAt: string, endAt: string) => axisPosition(snapshot, startAt, endAt);
  const segments = snapshot.dayAxis?.appSegments ?? [];
  const apps = [...new Map(segments.map((item) => [`${item.device}:${item.app}`, { app: item.app, device: item.device }])).values()];
  const appIndex = (device: string, name: string) => apps.findIndex((item) => item.device === device && item.app === name);
  return (
    <div className="day-axis" aria-label="当天 00:00 至 24:00 的时间轴">
      <div className="axis-ticks" aria-label="时间刻度">
        {AXIS_TICKS.map((tick, index) => <span key={tick} style={{ left: `${index * 12.5}%` }}>{tick}</span>)}
      </div>
      <div className="axis-tracks">
        <div className="day-bar" aria-label="过去的电脑和手机使用与未来的预留">
          {snapshot.dayAxis?.reserves.map((item) => <span
            className="axis-segment axis-segment--reserve" key={item.startAt}
            style={position(item.startAt, item.endAt)}
            title={`${formatTime(item.startAt)}—${formatTime(item.endAt)} 预计预留`} />)}
          {snapshot.calendarEvents.filter((item) => item.blocksTime && item.endAt > snapshot.generatedAt).map((item) => <span
            className="axis-segment axis-segment--calendar" key={`calendar-${item.id}`}
            style={position(item.startAt > snapshot.generatedAt ? item.startAt : snapshot.generatedAt, item.endAt)}
            title={`${formatTime(item.startAt)}—${formatTime(item.endAt)} ${item.subject} · 日历计划`} />)}
          {segments.map((item) => {
            const index = appIndex(item.device, item.app);
            const width = axisPercent(snapshot, item.endAt) - axisPercent(snapshot, item.startAt);
            return <span className={`axis-segment axis-app axis-app-tone-${index % 6}`}
              key={`${item.device}-${item.app}-${item.startAt}`} style={position(item.startAt, item.endAt)}
              aria-label={`${axisDeviceLabel(item.device)} ${item.app}`}
              title={`${formatTime(item.startAt)}—${formatTime(item.endAt)} ${item.app} · ${formatMinutes(item.minutes)}`}>
              {width >= 2.5 ? index + 1 : ''}
            </span>;
          })}
        </div>
        <span className="axis-now" style={{ left: `${axisPercent(snapshot, snapshot.generatedAt)}%` }}
          aria-label={`现在 ${formatTime(snapshot.generatedAt)}`}><span>现在</span></span>
      </div>
      <div className="axis-key" aria-label="电脑与手机软件图例">
        {apps.map((item, index) => <span className="axis-app-key" key={`${item.device}-${item.app}`}>
          <i className={`axis-app-tone-${index % 6}`} />{index + 1} {axisDeviceLabel(item.device)}·{item.app}
        </span>)}
      </div>
      <div className="axis-calendar-summary" title={calendarSummary(snapshot)}><i className="axis-segment--calendar" />{calendarSummary(snapshot)}</div>
    </div>
  );
}

function axisDeviceLabel(device: 'phone' | 'computer' | 'combined'): string {
  return { phone: '手机', computer: '电脑', combined: '双端' }[device];
}

function AllocationChart({ allocation, elapsedMinutes }: { allocation: AllocationSnapshot; elapsedMinutes: number }) {
  const slices = allocation.categories.map((category, index) => {
    const precedingSeconds = allocation.categories.slice(0, index).reduce((sum, item) => sum + item.seconds, 0);
    const angle = -Math.PI / 2 + precedingSeconds / allocation.totalSeconds * Math.PI * 2;
    const sweep = category.seconds / allocation.totalSeconds * Math.PI * 2;
    const path = category.seconds > 0 ? donutPath(angle, sweep) : '';
    return { category, path, middle: angle + sweep / 2 };
  });
  const entertainment = slices.find((item) => item.category.id === 'entertainment' && item.category.seconds > 0);

  return (
    <div className="allocation-content"><div className="allocation-body">
      <div className="ring-wrap">
        <svg className="allocation-ring" viewBox="0 0 200 200" role="img" aria-label="全天时间分配">
          <desc>{allocation.categories.map((item) => `${item.label}${formatMinutes(item.minutes)}，占${item.percent.toFixed(1)}%`).join('；')}</desc>
          <AllocationPatterns />
          {slices.filter((item) => item.path).map(({ category, path }) => <path
            key={category.id} d={path} fill={categoryFill(category.id)}>
            <title>{`${category.label} · ${formatMinutes(category.minutes)} · ${category.percent.toFixed(1)}%`}</title>
          </path>)}
          {entertainment && (allocation.entertainmentBreakdown?.length ?? 0) > 0 && <path className="entertainment-ring-link"
            d={`M${100 + 97 * Math.cos(entertainment.middle)},${100 + 97 * Math.sin(entertainment.middle)} L${100 + 108 * Math.cos(entertainment.middle)},${100 + 108 * Math.sin(entertainment.middle)} L5,218 L5,235`} />}
        </svg>
        <div className="ring-center"><span>全天</span><strong>24 小时</strong><span>已过 {compactMinutes(elapsedMinutes)}</span></div>
      </div>
      <div className="allocation-legend" aria-label="七类时间与比例">
        {allocation.categories.map((category) => <div className="allocation-legend-row" key={category.id}>
          <i className={`allocation-swatch allocation-swatch--${category.id}`} aria-hidden="true" />
          <span className="allocation-category">{category.label}</span>
          <span className="allocation-duration">{compactMinutes(category.minutes)}</span>
          <span className="allocation-percent">{category.percent.toFixed(1)}%</span>
        </div>)}
      </div>
    </div><EntertainmentBreakdown allocation={allocation} /></div>
  );
}

function EntertainmentBreakdown({ allocation }: { allocation: AllocationSnapshot }) {
  const entries = [...(allocation.entertainmentBreakdown ?? [])].filter((item) => item.seconds > 0).sort((a, b) => b.seconds - a.seconds);
  if (!entries.length) return <div className="entertainment-breakdown entertainment-breakdown--empty">娱乐构成暂无记录</div>;
  const visible = entries.slice(0, 3);
  const rest = entries.slice(3);
  const detail = rest.length ? [...visible, { name: '其余应用', device: 'unknown', seconds: rest.reduce((sum, item) => sum + item.seconds, 0), minutes: rest.reduce((sum, item) => sum + item.minutes, 0) }] : visible;
  return <div className="entertainment-breakdown" aria-label="娱乐时间具体构成">
    <span className="entertainment-branch" aria-hidden="true" />
    <strong>娱乐构成</strong>
    <div>{detail.map((item) => <span key={`${item.device}-${item.name}`}>
      <span className="entertainment-app" title={item.name}>{item.device === 'computer' ? '电脑·' : item.device === 'phone' ? '手机·' : ''}{item.name}</span>
      <span>{compactMinutes(item.minutes)}</span>
    </span>)}</div>
  </div>;
}

function AllocationPatterns() {
  return <defs>
    <pattern id="allocation-entertainment" patternUnits="userSpaceOnUse" width="6" height="6">
      <rect width="6" height="6" fill="#fff" /><rect width="3" height="6" fill="#181818" />
    </pattern>
    <pattern id="allocation-reading" patternUnits="userSpaceOnUse" width="6" height="6">
      <rect width="6" height="6" fill="#fff" /><path d="M0 6L6 0M-2 2L2 -2M4 8L8 4" stroke="#181818" strokeWidth="1.3" />
    </pattern>
    <pattern id="allocation-other" patternUnits="userSpaceOnUse" width="6" height="6">
      <rect width="6" height="6" fill="#fff" /><circle cx="3" cy="3" r="1" fill="#181818" />
    </pattern>
  </defs>;
}

function categoryFill(id: AllocationCategory['id']): string {
  return {
    sleep: '#181818', study: '#777', entertainment: 'url(#allocation-entertainment)',
    reading: 'url(#allocation-reading)', daily: '#c4c4c4', other: 'url(#allocation-other)', future: '#ededed',
  }[id];
}

function donutPath(angle: number, sweep: number): string {
  if (sweep >= Math.PI * 2 - 1e-9) {
    return 'M100 5A95 95 0 1 1 100 195A95 95 0 1 1 100 5L100 35A65 65 0 1 0 100 165A65 65 0 1 0 100 35Z';
  }
  const point = (radius: number, at: number) => `${100 + radius * Math.cos(at)},${100 + radius * Math.sin(at)}`;
  const large = sweep > Math.PI ? 1 : 0;
  return `M${point(95, angle)}A95 95 0 ${large} 1 ${point(95, angle + sweep)}L${point(65, angle + sweep)}A65 65 0 ${large} 0 ${point(65, angle)}Z`;
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

function SleepDetail({ sleep }: { sleep?: SleepSummary | null }) {
  if (!sleep || sleep.estimated || !sleep.startAt || !sleep.endAt || !sleep.intervals.length) return null;
  return <div className="sleep-detail" aria-label="今日入睡到起床的时间跨度">
    <strong>寝起 {compactMinutes(sleep.minutes)}</strong>
    <span>入睡 {formatTime(sleep.startAt)} → 起床 {formatTime(sleep.endAt)}</span>
  </div>;
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

function calendarSummary(snapshot: TimeMirrorSnapshot): string {
  const ordered = [...snapshot.calendarEvents].sort((a, b) => a.startAt.localeCompare(b.startAt));
  const event = ordered.find((item) => item.endAt > snapshot.generatedAt) ?? ordered.at(-1);
  if (event) return `日程 ${formatTime(event.startAt)}—${formatTime(event.endAt)} ${event.subject}`;
  return ['ok', 'cached'].includes(snapshot.sources.calendar?.status ?? '') ? '今日日历暂无日程' : '日历暂不可用';
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

function compactMinutes(minutes: number): string {
  const rounded = Math.max(0, Math.round(minutes));
  if (rounded < 60) return `${rounded}m`;
  return `${Math.floor(rounded / 60)}h${rounded % 60 ? String(rounded % 60).padStart(2, '0') : ''}`;
}

function axisPercent(snapshot: TimeMirrorSnapshot, at: string): number {
  const start = Date.parse(snapshot.dayStart);
  return Math.max(0, Math.min(100, (Date.parse(at) - start) / (Date.parse(snapshot.dayEnd) - start) * 100));
}

function axisPosition(snapshot: TimeMirrorSnapshot, startAt: string, endAt: string) {
  const left = axisPercent(snapshot, startAt);
  return { left: `${left}%`, width: `${Math.max(0, axisPercent(snapshot, endAt) - left)}%` };
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
    <header className="page-header"><div><p className="eyebrow">TIME MIRROR</p><h1>自动牛马机</h1></div></header>
    <section className="empty-state"><strong>本机快照暂时不可用</strong><p>请运行 start-dashboard.cmd 后刷新。</p></section>
  </main>;
}

