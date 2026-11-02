import { AllocationCategory, AllocationSnapshot, SleepSummary, TimeMirrorSnapshot, formatMinutes, formatTime } from './snapshot';

const AXIS_TICKS = ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00', '18:00', '21:00', '24:00'];

export function TimeValue({ minutes }: { minutes: number }) {
  const rounded = Math.max(0, Math.round(minutes));
  const hours = Math.floor(rounded / 60);
  return <strong className="remaining-value" aria-label={`约 ${formatMinutes(rounded)}`}>
    {hours > 0 && <>{hours}<small>小时</small></>}
    {(rounded % 60 > 0 || hours === 0) && <>{rounded % 60}<small>分</small></>}
  </strong>;
}

export function DayAxis({ snapshot }: { snapshot: TimeMirrorSnapshot }) {
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

export function AllocationChart({ allocation, elapsedMinutes }: { allocation: AllocationSnapshot; elapsedMinutes: number }) {
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

export function SleepDetail({ sleep }: { sleep?: SleepSummary | null }) {
  if (!sleep || sleep.estimated || !sleep.startAt || !sleep.endAt || !sleep.intervals.length) return null;
  return <div className="sleep-detail" aria-label="今日入睡到起床的时间跨度">
    <strong>寝起 {compactMinutes(sleep.minutes)}</strong>
    <span>入睡 {formatTime(sleep.startAt)} → 起床 {formatTime(sleep.endAt)}</span>
  </div>;
}

function calendarSummary(snapshot: TimeMirrorSnapshot): string {
  const ordered = [...snapshot.calendarEvents].sort((a, b) => a.startAt.localeCompare(b.startAt));
  const event = ordered.find((item) => item.endAt > snapshot.generatedAt) ?? ordered.at(-1);
  if (event) return `日程 ${formatTime(event.startAt)}—${formatTime(event.endAt)} ${event.subject}`;
  return ['ok', 'cached'].includes(snapshot.sources.calendar?.status ?? '') ? '今日日历暂无日程' : '日历暂不可用';
}

export function compactMinutes(minutes: number): string {
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
